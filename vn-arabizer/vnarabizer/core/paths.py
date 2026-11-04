"""Where the app keeps its own data. Nothing is written inside game folders
except the patch files the user explicitly applies."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def data_dir() -> Path:
    override = os.environ.get("VNARABIZER_HOME")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "VNArabizer"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "VNArabizer"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "vnarabizer"
    base.mkdir(parents=True, exist_ok=True)
    return base


def models_dir() -> Path:
    p = data_dir() / "models"
    p.mkdir(parents=True, exist_ok=True)
    return p


def projects_dir() -> Path:
    p = data_dir() / "projects"
    p.mkdir(parents=True, exist_ok=True)
    return p


def assets_dir() -> Path:
    # Works both from source and from a PyInstaller bundle.
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base) / "vnarabizer" / "assets"
    return Path(__file__).resolve().parent.parent / "assets"
