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


def untranslatable_reason(text: str) -> str | None:
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
    if not _strip_markup(s).strip():
        return "markup only"
    return None


def _strip_markup(text: str) -> str:
    return _RENPY_TOKEN.sub("", text)


@dataclass
class Masked:
    """A line prepared for translation."""

    original: str
    prefix: str  # leading whitespace + leading tokens, kept verbatim
    suffix: str  # trailing tokens + trailing whitespace, kept verbatim
    body: str  # prose with ⟦n⟧ placeholders for inner tokens
    tokens: list[str] = field(default_factory=list)

    @property
    def needs_translation(self) -> bool:
        return bool(_PLACEHOLDER_RE.sub("", self.body).strip())


def mask(text: str) -> Masked:
    """Split ``text`` into verbatim edges and a placeholder-marked body."""
    pieces: list[tuple[bool, str]] = []  # (is_token, text)
    pos = 0
    for m in _RENPY_TOKEN.finditer(text):
        if m.start() > pos:
            pieces.append((False, text[pos : m.start()]))
        pieces.append((True, m.group(0)))
        pos = m.end()
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

    return Masked(text, "".join(prefix_parts), "".join(suffix_parts), "".join(body_parts), tokens)


def _escape_prose(s: str) -> str:
    """Make translator output safe to show literally in Ren'Py."""
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
        out.append(_escape_prose(translated_body[pos : m.start()]))
        out.append(masked.tokens[int(m.group(1))])
        pos = m.end()
    out.append(_escape_prose(translated_body[pos:]))
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
                out.append(lead + _escape_prose(src.strip()) + trail)
            else:
                out.append(p)
        else:
            out.append(masked.tokens[int(p)])
    body = "".join(out).strip()
    return masked.prefix + body + masked.suffix


def validate(original: str, translated: str) -> list[str]:
    """Return a list of problems; empty means the line is safe to write."""
    problems: list[str] = []
    if not translated.strip():
        problems.append("empty translation")
    a = Counter(_RENPY_TOKEN.findall(original))
    b = Counter(_RENPY_TOKEN.findall(translated))
    # Escapes we add ourselves are allowed to appear.
    for k in ("{{", "[["):
        b.pop(k, None)
        a.pop(k, None)
    if a != b:
        missing = a - b
        extra = b - a
        if missing:
            problems.append("missing markup: " + " ".join(sorted(missing)))
        if extra:
            problems.append("unexpected markup: " + " ".join(sorted(extra)))
    if _unbalanced(translated):
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
