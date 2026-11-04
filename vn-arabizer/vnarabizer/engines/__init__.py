"""Game engine plugins. Each plugin knows how to detect, extract and patch one engine."""

from __future__ import annotations

from pathlib import Path

from .base import Engine, Entry
from .renpy import RenPyEngine

ENGINES: list[type[Engine]] = [RenPyEngine]


def detect(path: str | Path) -> Engine | None:
    """Return an engine instance for the game at ``path``, or None if unknown."""
    p = Path(path)
    if p.is_file():
        p = p.parent
    for cls in ENGINES:
        inst = cls.detect(p)
        if inst is not None:
            return inst
    return None


__all__ = ["ENGINES", "Engine", "Entry", "detect"]
