"""Common interface for engine plugins."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

Progress = Callable[[str, float], None]  # (message, fraction 0..1)


def _noop(msg: str, frac: float) -> None:
    pass


@dataclass
class Entry:
    """One translatable string found in a game.

    ``channel`` tells the patch writer how the engine will look it up:
    for Ren'Py, ``say`` is dialogue and menu choices (matched by the raw
    script text) and ``strings`` is interface text and character names
    (matched through Ren'Py's string translation table).
    """

    channel: str
    original: str
    kind: str  # dialogue, menu, name, ui
    speaker: str = ""
    file: str = ""
    line: int = 0
    count: int = 1
    order: int = 0
    skip_reason: str | None = None
    extra: dict = field(default_factory=dict)


class Engine(ABC):
    name: str = "?"

    def __init__(self, root: Path):
        self.root = Path(root)

    @classmethod
    @abstractmethod
    def detect(cls, path: Path) -> "Engine | None": ...

    @property
    def version(self) -> str:
        return ""

    def describe(self) -> str:
        v = f" {self.version}" if self.version else ""
        return f"{self.name}{v}"

    @abstractmethod
    def extract(self, progress: Progress = _noop) -> list[Entry]: ...

    @abstractmethod
    def apply(self, translations: dict[tuple[str, str], str], progress: Progress = _noop) -> list[str]:
        """Install the translation into the game. Returns the files written."""

    @abstractmethod
    def remove(self) -> list[str]:
        """Undo ``apply``. Returns the files removed."""

    def is_applied(self) -> bool:
        return False
