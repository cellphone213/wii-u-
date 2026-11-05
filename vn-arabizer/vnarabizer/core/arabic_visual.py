"""Arabic for engines that only draw left-to-right, one character at a time.

Most Japanese engines (CatSystem2, KiriKiri, BGI, Siglus...) place every
character from left to right and never join letters. To show correct
Arabic there, the text is prepared in advance:

1. wrapped into lines by width (the engine must not wrap it again),
2. every letter replaced by its joined form (Arabic Presentation Forms),
3. each line put into visual order (right-to-left reading, stored left-to-right).

Engine control codes inside the text are swapped for private-use
placeholders during steps 2-3 so they are neither mirrored nor reversed.
"""

from __future__ import annotations

import re

import arabic_reshaper
from bidi.algorithm import get_display

from ..engines.rpgm.wrap import text_width

_RESHAPER = arabic_reshaper.ArabicReshaper(
    # Diacritics are dropped: many engines give every character its own cell.
    configuration={"delete_harakat": True, "support_ligatures": True, "use_unshaped_instead_of_isolated": True}
)
_ARABIC = re.compile(r"[؀-ۿ]")
PUA = 0xE000
# Stand-ins for control codes while shaping and reordering. They are strong
# right-to-left letters (Samaritan, NKo, Hebrew) that never occur in game text,
# so a code that prints a word (a name, a number) sits where an Arabic word
# would: "%1$s %2$s" keeps its reading order. Private-use characters (which are
# left-to-right) are only used past this list.
_HOLDERS = [chr(c) for c in [*range(0x0800, 0x0816), *range(0x07CA, 0x07EB), *range(0x05D0, 0x05EB)]]
_HOLDER_INDEX = {c: i for i, c in enumerate(_HOLDERS)}


def has_arabic(text: str) -> bool:
    return bool(_ARABIC.search(text))


def wrap(text: str, max_em: float, token_rx: re.Pattern | None = None) -> list[str]:
    """Greedy word wrap by width in ems (1 em = font size)."""
    def width(s: str) -> float:
        if token_rx is not None:
            s = token_rx.sub("", s)
        return text_width(s, 1)

    out: list[str] = []
    for para in text.split("\n"):
        line = ""
        for word in para.split(" "):
            cand = word if not line else f"{line} {word}"
            if line and width(cand) > max_em:
                out.append(line)
                line = word
            else:
                line = cand
        out.append(line)
    return out


def visual_line(line: str, token_rx: re.Pattern | None = None) -> str:
    """One line of logical-order Arabic -> joined glyphs in left-to-right display order."""
    if not has_arabic(line):
        return line
    line = normalize_punct(line)
    tokens: list[str] = []

    def hide(m):
        tokens.append(m.group(0))
        n = len(tokens) - 1
        return _HOLDERS[n] if n < len(_HOLDERS) else chr(PUA + n - len(_HOLDERS))

    def show(m):
        c = m.group(0)
        i = _HOLDER_INDEX.get(c)
        if i is None:
            i = len(_HOLDERS) + ord(c) - PUA
        return tokens[i] if i < len(tokens) else c

    work = token_rx.sub(hide, line) if token_rx is not None else line
    work = get_display(_RESHAPER.reshape(work), base_dir="R")
    if tokens:
        work = re.sub(r"[\u0800-\u0815\u07ca-\u07ea\u05d0-\u05ea\ue000-\uf8ff]", show, work)
    return work


# Japanese punctuation the Arabic font does not have -> equivalents it has.
_PUNCT = str.maketrans({
    "「": "\"", "」": "\"", "『": "\"", "』": "\"", "（": "(", "）": ")",
    "、": "\u060c", "。": ".", "！": "!", "？": "\u061f", "：": ":", "；": "\u061b",
    "　": " ", "～": "~", "・": "\u00b7", "…": "\u2026", "‥": "\u2025", "ー": "-", "―": "-", "─": "-",
})


def normalize_punct(text: str) -> str:
    return text.translate(_PUNCT)


def _split_edges(text: str, token_rx: re.Pattern | None) -> tuple[str, str, str]:
    """Codes at the very start/end of a message (click waits, page breaks) stay put."""
    if token_rx is None:
        return "", text, ""
    lead = trail = ""
    while True:
        m = token_rx.match(text)
        if not m or m.start() != 0 or not m.group(0):
            break
        lead += m.group(0)
        text = text[m.end():]
    while True:
        last = None
        for m in token_rx.finditer(text):
            last = m
        if last is None or last.end() != len(text.rstrip()) or not last.group(0):
            break
        trail = text[last.start():].rstrip() + trail
        text = text[: last.start()]
    return lead, text, trail


def visual_text(
    text: str, max_em: float, token_rx: re.Pattern | None = None, newline: str = "\n", edge_rx: re.Pattern | None = None
) -> str:
    """Wrap, shape and reorder a whole message for a left-to-right engine.

    Codes matching ``edge_rx`` (default: any token) at the very start or end
    of the message stay there instead of moving with the text.
    """
    if not has_arabic(text):
        return text
    lead, body, trail = _split_edges(normalize_punct(text), edge_rx if edge_rx is not None else token_rx)
    lines = [visual_line(l, token_rx) for l in wrap(body.strip(), max_em, token_rx)]
    lines[0] = lead + lines[0]
    lines[-1] = lines[-1] + trail
    return newline.join(lines)
