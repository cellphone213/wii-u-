"""Japanese visual novel engines handled through VNTextPatch."""

from .catsystem2 import CatSystem2Engine
from .folder import ScriptFolderEngine
from .kirikiri import KiriKiriEngine
from .packed import (
    PACKED_ENGINES,
    AdvHDEngine,
    ArtemisEngine,
    BGIEngine,
    KaguyaEngine,
    MajiroEngine,
    SoftpalEngine,
    YurisEngine,
)

__all__ = [
    "PACKED_ENGINES", "AdvHDEngine", "ArtemisEngine", "BGIEngine", "CatSystem2Engine", "KaguyaEngine",
    "KiriKiriEngine", "MajiroEngine", "ScriptFolderEngine", "SoftpalEngine", "YurisEngine",
]
