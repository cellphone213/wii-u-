"""Game engine plugins. Each plugin knows how to detect, extract and patch one engine."""

from __future__ import annotations

from pathlib import Path

from .base import Engine, Entry
from .renpy import RenPyEngine
from .rpgm import RPGMakerEngine
from .twine import TwineEngine
from .vntp import CatSystem2Engine, KiriKiriEngine, ScriptFolderEngine

# Order matters: specific engines first, the generic script folder last.
ENGINES: list[type[Engine]] = [RenPyEngine, RPGMakerEngine, TwineEngine, CatSystem2Engine, KiriKiriEngine, ScriptFolderEngine]


def detect(path: str | Path) -> Engine | None:
    """Return an engine instance for the game at ``path``, or None if unknown."""
    p = Path(path)
    if p.is_file():
        # A Twine game can be picked as its HTML file directly.
        if p.suffix.lower() in (".html", ".htm"):
            inst = TwineEngine.detect(p)
            if inst is not None:
                return inst
        p = p.parent
    for cls in ENGINES:
        inst = cls.detect(p)
        if inst is not None:
            return inst
    return None


__all__ = ["ENGINES", "Engine", "Entry", "detect"]
