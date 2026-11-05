"""Japanese visual novel engines handled through VNTextPatch."""

from .catsystem2 import CatSystem2Engine
from .folder import ScriptFolderEngine
from .kirikiri import KiriKiriEngine

__all__ = ["CatSystem2Engine", "KiriKiriEngine", "ScriptFolderEngine"]
