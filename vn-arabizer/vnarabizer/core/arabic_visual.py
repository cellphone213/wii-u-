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
    tokens: list[str] = []

    def hide(m):
        tokens.append(m.group(0))
        return chr(PUA + len(tokens) - 1)

    work = token_rx.sub(hide, line) if token_rx is not None else line
    work = get_display(_RESHAPER.reshape(work), base_dir="R")
    if tokens:
        work = re.sub(r"[-]", lambda m: tokens[ord(m.group(0)) - PUA], work)
    return work


def visual_text(text: str, max_em: float, token_rx: re.Pattern | None = None, newline: str = "\n") -> str:
    """Wrap, shape and reorder a whole message for a left-to-right engine."""
    if not has_arabic(text):
        return text
    return newline.join(visual_line(l, token_rx) for l in wrap(text, max_em, token_rx))
