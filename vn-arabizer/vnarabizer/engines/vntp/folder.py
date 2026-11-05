"""A folder of extracted script files from any engine VNTextPatch supports.

Most games are opened directly (their archives are unpacked and rebuilt by
VN Arabizer itself). This mode is for the rest: a folder of script files
already taken out of the game (QLIE, Silky's, Musica, Propeller, SystemNNN,
dumped encrypted KiriKiri...). VN Arabizer writes the translated scripts to
``<folder> (Arabic)``; put them back with the engine's loose-file override
or a packer, and copy the files in ``_put_next_to_game_exe`` next to the
game's .exe.
"""

from __future__ import annotations

import shutil
from collections import Counter
from pathlib import Path

from ..base import Progress
from . import tool
from .base import BACKUP_DIR, VNTPEngine

RUNTIME_DIR = "_put_next_to_game_exe"


class ScriptFolderEngine(VNTPEngine):
    name = "Script folder"

    def __init__(self, root: Path, ext: str, fmt: str | None = None):
        super().__init__(root)
        self.ext = ext
        self.fmt = fmt

    @classmethod
    def detect(cls, path: Path, fmt: str | None = None) -> "ScriptFolderEngine | None":
        p = Path(path)
        if not p.is_dir():
            return None
        if fmt:
            return cls(p, "", fmt)
        counts = Counter(f.suffix.lower() for f in p.rglob("*") if f.is_file() and f.suffix.lower() in tool.AUTO_FORMATS)
        if not counts:
            return None
        ext = counts.most_common(1)[0][0]
        return cls(p, ext)

    def describe(self) -> str:
        if self.fmt:
            return f"{tool.EXPLICIT_FORMATS.get(self.fmt, self.fmt)} — extracted scripts"
        return f"{tool.AUTO_FORMATS.get(self.ext, self.ext)} — extracted scripts ({self.ext})"

    @property
    def out_dir(self) -> Path:
        return self.root.parent / f"{self.root.name} (Arabic)"

    def stage_scripts(self, dest: Path) -> None:
        for f in self.root.rglob("*"):
            if not f.is_file() or BACKUP_DIR in f.parts:
                continue
            if self.fmt or f.suffix.lower() == self.ext or (self.ext == ".nnn" and f.suffix.lower() == ".spt"):
                target = dest / f.relative_to(self.root)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, target)

    def _game_exe(self) -> Path | None:
        for parent in [self.root, *self.root.parents][:4]:
            exe = tool.find_game_exe(parent)
            if exe is not None:
                return exe
        return None

    def install(self, patched: Path, sjis_ext: Path, progress: Progress) -> list[str]:
        out = self.out_dir
        shutil.rmtree(out, ignore_errors=True)
        shutil.copytree(patched, out)
        runtime = out / RUNTIME_DIR
        runtime.mkdir()
        # Every proxy variant: the user keeps the one their game loads.
        for name in tool.PROXIES:
            try:
                shutil.copy2(tool.proxy_dll(name), runtime / f"{name}.dll")
            except tool.ToolMissing:
                pass
        from ...core.paths import assets_dir

        shutil.copy2(assets_dir() / "fonts" / "VNArabic-Regular.ttf", runtime / "VN Arabic.ttf")
        if sjis_ext.exists() and sjis_ext.stat().st_size:
            shutil.copy2(sjis_ext, runtime / "sjis_ext.bin")
        exe = self._game_exe()
        proxy = tool.choose_proxy(exe) if exe else None
        (runtime / "README.txt").write_text(
            "VN Arabizer\n\n"
            "1. Put the translated scripts back into the game (loose-file override or the engine's packer).\n"
            "2. Copy 'VN Arabic.ttf'" + (", 'sjis_ext.bin'" if (runtime / "sjis_ext.bin").exists() else "")
            + " and ONE proxy DLL next to the game's .exe.\n"
            + (f"   For {exe.name} use: {proxy}.dll\n" if proxy else "   Use a DLL whose name the game's .exe imports (d2d1, winmm, version...).\n"),
            "utf-8",
        )
        self.warnings.append(f"Translated scripts were written to: {out}")
        return [str(out)]

    def remove(self) -> list[str]:
        if self.out_dir.is_dir():
            shutil.rmtree(self.out_dir)
            return [str(self.out_dir)]
        return []

    def is_applied(self) -> bool:
        return self.out_dir.is_dir()
