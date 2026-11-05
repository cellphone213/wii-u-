"""CatSystem2 (Frontwing, Purple Software, etc.).

Scripts are .cst files inside ``scene.int`` (a KIF archive, usually
encrypted with a key stored in the game executable). The patched archive is
written unencrypted, which CatSystem2 also accepts. Text that Shift-JIS
cannot hold is written by VNTextPatch as CatSystem2 ``\\$code;`` escapes;
VNTextProxy draws it with the Arabic font.

E-mote: newer CatSystem2 games animate characters with E-mote (.psb)
files. Those contain no dialogue, so nothing there needs translating.
"""

from __future__ import annotations

from pathlib import Path

from ...archives.kif import KifArchive, KifError, passphrase_from_exe, write_kif
from ..base import Progress
from . import tool
from .base import VNTPEngine, safe_name


def _is_kif(p: Path) -> bool:
    try:
        with open(p, "rb") as f:
            return f.read(4) == b"KIF\x00"
    except OSError:
        return False


class CatSystem2Engine(VNTPEngine):
    name = "CatSystem2"
    line_em = 26.0

    def __init__(self, root: Path, scene: Path):
        super().__init__(root)
        self.scene = scene
        self._pass: str | None | bool = False

    @classmethod
    def detect(cls, path: Path) -> "CatSystem2Engine | None":
        p = Path(path)
        for name in ("scene.int", "Scene.int", "SCENE.INT"):
            if (p / name).is_file() and _is_kif(p / name):
                return cls(p, p / name)
        return None

    def describe(self) -> str:
        return "CatSystem2"

    @property
    def exe(self) -> Path | None:
        exes = sorted(self.root.glob("*.exe"), key=lambda e: e.stat().st_size, reverse=True)
        for e in exes:
            if passphrase_from_exe(e) is not None:
                return e
        return tool.find_game_exe(self.root)

    def passphrase(self) -> str | None:
        if self._pass is False:
            self._pass = None
            for e in sorted(self.root.glob("*.exe"), key=lambda e: e.stat().st_size, reverse=True):
                pw = passphrase_from_exe(e)
                if pw:
                    self._pass = pw
                    break
        return self._pass  # type: ignore[return-value]

    def _archive(self) -> KifArchive:
        try:
            return KifArchive(self.source(self.scene), self.passphrase())
        except KifError as e:
            raise RuntimeError(
                f"Cannot open {self.scene.name}: {e}. If the game's key is not in its .exe, "
                "extract the .cst files with GARbro and open that folder instead."
            ) from e

    def stage_scripts(self, dest: Path) -> None:
        arc = self._archive()
        n = 0
        for e in arc.entries:
            if e.name.lower().endswith(".cst"):
                (dest / safe_name(e.name)).write_bytes(arc.read(e))
                n += 1
        if not n:
            raise RuntimeError(f"No .cst scripts inside {self.scene.name}.")

    def install(self, patched: Path, sjis_ext: Path, progress: Progress) -> list[str]:
        arc = self._archive()
        replacements = {p.name.lower(): p.read_bytes() for p in patched.glob("*.cst")}
        files = []
        for e in arc.entries:
            data = replacements.get(safe_name(e.name).lower()) or arc.read(e)
            files.append((e.name, data))
        self.backup(self.scene)
        write_kif(self.scene, files)
        added = self.deploy_runtime(self.exe, sjis_ext)
        return [self.scene.name] + [a.name for a in added]
