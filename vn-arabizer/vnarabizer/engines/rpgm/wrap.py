"""Wrap translated RPG Maker text to the width of its window.

RPG Maker message boxes are hard-wrapped: every line is its own event
command. Arabic text is usually a different length than the original, so
the translation is re-wrapped using the advance widths of the bundled
Arabic font. Estimates err on the wide side so lines never overflow.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache

from ...core.paths import assets_dir

_TOKEN = re.compile(r"\\[A-Za-z]+\[[^\]\n]*\]|\\[A-Za-z]+<[^>\n]*>|\\[A-Za-z]+|\\[$.|^!><{}\\]|<[A-Za-z/!][^<>\n]{0,60}>")


@lru_cache(maxsize=1)
def _widths() -> dict[int, int]:
    f = assets_dir() / "fonts" / "VNArabic-Regular.widths.json"
    try:
        return {int(k): v for k, v in json.loads(f.read_text("utf-8")).items()}
    except OSError:
        return {}


def _token_width(tok: str, size: int) -> float:
    t = tok.upper()
    if t.startswith("\\I["):
        return 36  # icon + spacing
    if t.startswith(("\\N[", "\\P[")):
        return size * 0.6 * 8  # an actor name
    if t.startswith("\\V["):
        return size * 0.6 * 4  # a number
    if t == "\\G":
        return size * 0.6 * 3
    return 0


def text_width(text: str, size: int) -> float:
    """Approximate drawn width in pixels at font ``size``."""
    w = _widths()
    total = 0.0
    pos = 0
    for m in _TOKEN.finditer(text):
        for ch in text[pos : m.start()]:
            total += w.get(ord(ch), 1000) * size / 1000
        total += _token_width(m.group(0), size)
        pos = m.end()
    for ch in text[pos:]:
        total += w.get(ord(ch), 1000) * size / 1000
    return total


def wrap(text: str, width: float, size: int) -> list[str]:
    """Split ``text`` into lines no wider than ``width`` pixels.

    Existing line breaks are kept (a translator or the user may place them
    on purpose). Words longer than a line are kept whole.
    """
    out: list[str] = []
    for para in text.split("\n"):
        words = para.split(" ")
        line = ""
        for word in words:
            cand = word if not line else line + " " + word
            if line and text_width(cand, size) > width:
                out.append(line)
                line = word
            else:
                line = cand
        out.append(line)
    return out
