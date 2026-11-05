"""Protect engine markup while text goes through a machine translator.

A line such as ``"{i}Hello{/i}, [player]!{w} Ready?"`` is split into
*tokens* (markup that must survive byte-for-byte) and *prose* (what the
translator may change). Before translation every token is swapped for a
numbered placeholder; afterwards the placeholders are swapped back and the
result is validated. Anything a translator invents that the engine would read
as markup (a stray ``[`` or ``{``) is escaped so it is shown literally.

Tokens are never sent to the translator, so file names, variable names and
tags cannot be "translated" into something that breaks the game.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

# Ren'Py markup, in priority order. Escaped braces/brackets first so that
# "{{" is not mistaken for the start of a tag.
_RENPY_TOKEN = re.compile(
    r"""
      \{\{                      # literal {
    | \[\[                      # literal [
    | %%                        # literal %
    | \{[^{}]*\}                # text tag: {b} {/b} {w=0.5} {color=#fff} {image=x.png}
    | \[[^\[\]]*\]              # interpolation: [player] [score!t]
    | %\([A-Za-z_][\w.]*\)[-#0 +]*\d*(?:\.\d+)?[diouxXeEfFgGcrsa]   # %(name)s
    | %[-#0 +]*\d*(?:\.\d+)?[diouxXeEfFgGcrsa]                     # %s %d %.2f
    | \n                        # explicit line break
    """,
    re.VERBOSE,
)

# RPG Maker MV/MZ message codes: \V[1] \N[2] \C[3] \I[64] \G \. \| \! \{ \}
# plus plugin codes (\FS[24], \n<Name>), plugin tags (<WordWrap>) and the
# %1 placeholders used in system messages.
_RPGM_TOKEN = re.compile(
    r"""
      \\[A-Za-z]+\[[^\]\n]*\]          # \C[2] \V[10] \FS[24]
    | \\[A-Za-z]+<[^>\n]*>                # \n<Name> (message plugins)
    | \\[A-Za-z]+                         # \G and other letter codes
    | \\[$.|^!><{}\\]                     # \. \| \! \> \< \^ \{ \} \\ \$
    | <[A-Za-z/!][^<>\n]{0,60}>            # plugin tags such as <WordWrap>, <br>
    | %\d+                                 # %1 in system messages
    """,
    re.VERBOSE,
)

# Codes in text extracted by VNTextPatch from Japanese engines: KiriKiri tags
# ([ruby text=...], [emb exp=...]), CatSystem2 escapes (\\fs, \\@, \\$12345;),
# angle-bracket and brace codes used by several engines.
_VNTP_TOKEN = re.compile(
    r"""
      \[[^\[\]\n]{1,80}\]
    | \\[A-Za-z]+\[[^\]\n]*\]
    | \\\$\d+;
    | \\[A-Za-z@]+
    | <[^<>\n]{1,60}>
    | \{[^{}\n]{1,60}\}
    """,
    re.VERBOSE,
)

SYNTAXES = {"renpy": _RENPY_TOKEN, "rpgm": _RPGM_TOKEN, "vntp": _VNTP_TOKEN}
# Twine markup needs a real tokenizer (nested quotes, multi-line macros).
TWINE = ("sugarcube", "harlowe")
# Entities our own escaping produces for Twine prose.
_TWINE_ESCAPES = {"&lt;", "&#36;", "&#91;", "&#93;", "&#40;", "&amp;"} | {f"&#{ord(p[0])};" for p in ("''", "//", "__", "~~", "^^", "==", "**", "@@")}


def token_spans(text: str, syntax: str) -> list[tuple[int, int]]:
    if syntax in TWINE:
        from . import twine_markup

        return twine_markup.spans(text, syntax)
    return [m.span() for m in SYNTAXES[syntax].finditer(text)]


def find_tokens(text: str, syntax: str) -> list[str]:
    return [text[a:b] for a, b in token_spans(text, syntax)]

_CJK = re.compile(r"[\u3000-\u30ff\u3400-\u9fff\uff00-\uffef]")


def _join_lines(text: str) -> str:
    """RPG Maker message boxes are hard-wrapped; the engine plugin re-wraps.

    Joins the original lines so the translator sees whole sentences
    (no space between Japanese/Chinese lines, a space otherwise).
    """
    lines = text.split("\n")
    out = lines[0]
    for nxt in lines[1:]:
        if out and nxt and _CJK.match(out[-1]) and _CJK.match(nxt[0]):
            out += nxt
        else:
            out += " " + nxt
    return out


# Tags that are pure formatting around the whole line; they are peeled off
# and re-attached so the translator sees clean prose.
_PLACEHOLDER = "⟦{}⟧"  # ⟦0⟧
_PLACEHOLDER_RE = re.compile(r"⟦\s*(\d+)\s*⟧")

# Strings that must never be translated at all.
_FILE_EXT = re.compile(
    r"\.(png|jpe?g|webp|gif|bmp|avif|svg|ogg|opus|mp3|wav|flac|m4a|webm|mp4|mkv|avi|ogv|"
    r"ttf|otf|ttc|woff2?|rpy|rpyc|rpa|rpym|rpymc|py|pyc|json|txt|csv|xml|ks|tjs|scn|nut|lua|"
    r"dat|bin|exe|dll)\s*$",
    re.IGNORECASE,
)
_PATHLIKE = re.compile(r"^[\w.\-]+(/[\w.\-]+)+/?$")
# Code-like names: snake_case, dotted.names, or CONSTANT_CASE.
_IDENTIFIER = re.compile(
    r"^(?:[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)+"
    r"|[A-Za-z0-9]*_[A-Za-z0-9_]*)$"
)
_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_NUMBERISH = re.compile(r"^[\s\d.,:;+\-*/%()=<>!?#]*$")


def untranslatable_reason(text: str, syntax: str = "renpy") -> str | None:
    """Return why ``text`` must be left alone, or None if it is prose.

    This is the guard that stops things like ``eileen happy.png`` or
    ``audio/theme.ogg`` from ever reaching a translator.
    """
    s = text.strip()
    if not s:
        return "empty"
    if _FILE_EXT.search(s):
        return "file name"
    if _PATHLIKE.match(s):
        return "path"
    if _COLOR.match(s):
        return "color code"
    if _NUMBERISH.match(s):
        return "numbers/symbols only"
    # "bg_room" or "persistent.flag" is code, not dialogue. Plain words
    # ("Yes", "Hmm...") are allowed through.
    if _IDENTIFIER.match(s):
        return "identifier"
    if not _strip_markup(s, syntax).strip():
        return "markup only"
    return None


def _strip_markup(text: str, syntax: str = "renpy") -> str:
    out, pos = [], 0
    for a, b in token_spans(text, syntax):
        out.append(text[pos:a])
        pos = b
    out.append(text[pos:])
    return "".join(out)


@dataclass
class Masked:
    """A line prepared for translation."""

    original: str
    prefix: str  # leading whitespace + leading tokens, kept verbatim
    suffix: str  # trailing tokens + trailing whitespace, kept verbatim
    body: str  # prose with ⟦n⟧ placeholders for inner tokens
    tokens: list[str] = field(default_factory=list)
    syntax: str = "renpy"

    @property
    def needs_translation(self) -> bool:
        return bool(_PLACEHOLDER_RE.sub("", self.body).strip())


def mask(text: str, syntax: str = "renpy") -> Masked:
    """Split ``text`` into verbatim edges and a placeholder-marked body."""
    original = text
    if syntax in ("rpgm", "vntp"):
        text = _join_lines(text.replace("\r\n", "\n"))
    pieces: list[tuple[bool, str]] = []  # (is_token, text)
    pos = 0
    for a, b in token_spans(text, syntax):
        if a > pos:
            pieces.append((False, text[pos:a]))
        pieces.append((True, text[a:b]))
        pos = b
    if pos < len(text):
        pieces.append((False, text[pos:]))

    # Peel tokens and whitespace off both ends.
    prefix_parts: list[str] = []
    while pieces and (pieces[0][0] or not pieces[0][1].strip()):
        prefix_parts.append(pieces.pop(0)[1])
    suffix_parts: list[str] = []
    while pieces and (pieces[-1][0] or not pieces[-1][1].strip()):
        suffix_parts.insert(0, pieces.pop()[1])

    # Keep surrounding whitespace of the prose in the edges too.
    if pieces and not pieces[0][0]:
        lead = pieces[0][1]
        stripped = lead.lstrip()
        prefix_parts.append(lead[: len(lead) - len(stripped)])
        pieces[0] = (False, stripped)
    if pieces and not pieces[-1][0]:
        trail = pieces[-1][1]
        stripped = trail.rstrip()
        suffix_parts.insert(0, trail[len(stripped) :])
        pieces[-1] = (False, stripped)

    tokens: list[str] = []
    body_parts: list[str] = []
    for is_token, s in pieces:
        if is_token:
            body_parts.append(_PLACEHOLDER.format(len(tokens)))
            tokens.append(s)
        else:
            body_parts.append(s)

    return Masked(original, "".join(prefix_parts), "".join(suffix_parts), "".join(body_parts), tokens, syntax)


def _escape_prose(s: str, syntax: str = "renpy") -> str:
    """Make translator output safe to show literally in the engine."""
    if syntax in TWINE:
        from . import twine_markup

        return twine_markup.escape_prose(s)
    if syntax == "vntp":
        # Characters that start codes in Japanese engines become full-width look-alikes.
        s = s.translate(str.maketrans({"[": "［", "]": "］", "\\": "＼", "<": "＜", ">": "＞", "{": "｛", "}": "｝"}))
        return re.sub(r"\s*\n\s*", " ", s)
    if syntax == "rpgm":
        # A stray backslash would start an escape code; "\\" shows one backslash.
        s = s.replace("\\", "\\\\")
        return re.sub(r"\s*\n\s*", " ", s)
    s = s.replace("{", "{{").replace("[", "[[")
    # A lone % can be read as a format code; the Arabic percent sign cannot.
    s = s.replace("%", "٪")
    # Translators sometimes return real line breaks; the engine wraps lines itself.
    s = re.sub(r"\s*\n\s*", " ", s)
    return s


class RestoreError(ValueError):
    pass


def unmask(masked: Masked, translated_body: str) -> str:
    """Put the tokens back into a translated body and validate the result."""
    found = [int(n) for n in _PLACEHOLDER_RE.findall(translated_body)]
    expected = list(range(len(masked.tokens)))
    if sorted(found) != expected:
        raise RestoreError(f"placeholders changed: expected {expected}, got {found}")

    out: list[str] = []
    pos = 0
    for m in _PLACEHOLDER_RE.finditer(translated_body):
        out.append(_escape_prose(translated_body[pos : m.start()], masked.syntax))
        out.append(masked.tokens[int(m.group(1))])
        pos = m.end()
    out.append(_escape_prose(translated_body[pos:], masked.syntax))
    body = "".join(out).strip()
    return masked.prefix + body + masked.suffix


def segments(masked: Masked) -> list[str]:
    """Prose pieces between placeholders, for the piece-by-piece fallback."""
    return _PLACEHOLDER_RE.split(masked.body)[0::2]


def join_segments(masked: Masked, translated_segments: list[str]) -> str:
    """Rebuild a line from separately translated prose pieces.

    Used when a translator mangles placeholders: every token is guaranteed
    to come back in its original position.
    """
    parts = _PLACEHOLDER_RE.split(masked.body)
    prose_idx = 0
    out: list[str] = []
    for i, p in enumerate(parts):
        if i % 2 == 0:
            src = translated_segments[prose_idx]
            prose_idx += 1
            if p.strip():
                # Keep the spacing the original had around this piece.
                lead = p[: len(p) - len(p.lstrip())]
                trail = p[len(p.rstrip()) :]
                out.append(lead + _escape_prose(src.strip(), masked.syntax) + trail)
            else:
                out.append(p)
        else:
            out.append(masked.tokens[int(p)])
    body = "".join(out).strip()
    return masked.prefix + body + masked.suffix


def validate(original: str, translated: str, syntax: str = "renpy") -> list[str]:
    """Return a list of problems; empty means the line is safe to write."""
    problems: list[str] = []
    if not translated.strip():
        problems.append("empty translation")
    a = Counter(find_tokens(original, syntax))
    b = Counter(find_tokens(translated, syntax))
    # Escapes we add ourselves are allowed to appear.
    for k in ("{{", "[[", "\\\\") if syntax not in TWINE else tuple(_TWINE_ESCAPES):
        b.pop(k, None)
        a.pop(k, None)
    if a != b:
        missing = a - b
        extra = b - a
        if missing:
            problems.append("missing markup: " + " ".join(sorted(missing)))
        if extra:
            problems.append("unexpected markup: " + " ".join(sorted(extra)))
    if syntax == "renpy" and _unbalanced(translated):
        problems.append("unbalanced { } or [ ]")
    return problems


def _unbalanced(s: str) -> bool:
    """True if an opening { or [ would be read as markup but never closes.

    A lone } or ] is shown literally by Ren'Py, so only openers matter.
    """
    s = s.replace("{{", "").replace("[[", "")
    s = re.sub(r"\{[^{}]*\}", "", s)
    s = re.sub(r"\[[^\[\]]*\]", "", s)
    return "{" in s or "[" in s


_EDGE_LEAD = re.compile(r"^[\s!?.,;:…\-–—\"'“”«»()]+")
_EDGE_TRAIL = re.compile(r"[\s!?.,;:…\-–—\"'“”«»()]+$")
_AR_PUNCT = str.maketrans({",": "،", "?": "؟", ";": "؛"})


def split_edges(piece: str) -> tuple[str, str, str]:
    """Split a prose piece into (leading punctuation, words, trailing punctuation).

    Machine translators often drop punctuation from short fragments such as
    "! Welcome to " between two tags; translating only the words and keeping
    the punctuation ourselves preserves the sentence shape.
    """
    m1 = _EDGE_LEAD.match(piece)
    lead = m1.group(0) if m1 else ""
    rest = piece[len(lead) :]
    m2 = _EDGE_TRAIL.search(rest)
    trail = m2.group(0) if m2 else ""
    core = rest[: len(rest) - len(trail)]
    return lead, core, trail


def arabic_punct(s: str) -> str:
    return s.translate(_AR_PUNCT)


# Markup that stands for a word in the sentence (a name, a number). These are
# swapped for numbers while translating so the sentence stays whole and the
# translator puts the word where it belongs in Arabic.
_WORD_TOKEN: dict = {
    "renpy": re.compile(r"^\[[^\[\]]+\]$|^%\(\w+\)[sd]$|^%[sd]$"),
    "rpgm": re.compile(r"^\\[NnPpVv]\[\d+\]$|^\\[Gg]$"),
    "vntp": re.compile(r"^\[emb\b"),  # KiriKiri [emb exp=...] prints a value
}
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def is_word_token(tok: str, syntax: str) -> bool:
    # Glossary terms are inserted as tokens too; they contain no markup at all.
    if token_spans(tok, syntax) != [(0, len(tok))]:
        return True
    if syntax in TWINE:
        from . import twine_markup

        return twine_markup.is_value_token(tok)
    return bool(_WORD_TOKEN[syntax].match(tok))


def sentinelize(m: Masked) -> tuple[str, dict[str, int]]:
    """Body with word-like placeholders replaced by unique numbers."""
    used = set(re.findall(r"\d+", m.original))
    smap: dict[str, int] = {}
    n = [7301]

    def rep(match):
        i = int(match.group(1))
        if not is_word_token(m.tokens[i], m.syntax):
            return match.group(0)
        while str(n[0]) in used:
            n[0] += 17
        s = str(n[0])
        used.add(s)
        smap[s] = i
        n[0] += 17
        return s

    return _PLACEHOLDER_RE.sub(rep, m.body), smap


def desentinelize(text: str, smap: dict[str, int]) -> str | None:
    """Put placeholders back for the numbers; None if any number was lost or doubled."""
    if not smap:
        return text
    t = text.translate(_DIGITS)
    for s, i in smap.items():
        pat = re.compile(r"(?<!\d)" + s + r"(?!\d)")
        if len(pat.findall(t)) != 1:
            return None
        t = pat.sub(_PLACEHOLDER.format(i), t)
    return t


def is_cjk(ch: str) -> bool:
    return bool(_CJK.match(ch))
