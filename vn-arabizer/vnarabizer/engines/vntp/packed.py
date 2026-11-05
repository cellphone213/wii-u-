"""Games whose scripts sit inside an archive VN Arabizer can unpack and repack itself.

No GARbro needed: the scripts are taken out of the archive into the work
folder, translated through VNTextPatch, and written back into a rebuilt
archive (the original is kept in ``vnarabizer_backup``).

  BGI / Ethornell   data01xxx.arc ("PackFile" / "BURIKO ARC20"), DSC-compressed scripts
  Majiro            scenario.arc (.mjo)
  AdvHD / WillPlus  Rio.arc (.ws2)
  Artemis           root.pfs (+ root.pfs.000 ... patches) (.ast / .asb)
  YU-RIS            ysbin.ypf / bn.ypf (.ybn)
  Softpal           data.pac (SCRIPT.SRC + TEXT.DAT + POINT.DAT)
  Kaguya            loose message .dat files ("[SCR-MESSAGE]")
"""

from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path

from ...archives.packs import AdvHdArc, BgiArc, MajiroArc, Pack, PackError, PfsArc, SoftpalPac, YpfArc, open_pack
from ..base import Progress
from . import tool
from .base import BACKUP_DIR, VNTPEngine, safe_name


class PackedEngine(VNTPEngine):
    pack_type: type[Pack] = Pack
    patterns: tuple[str, ...] = ()
    #: Put every script at the top of the work folder (VNTextPatch needs that for YU-RIS).
    flatten = False

    # -- archives -------------------------------------------------------------------
    @classmethod
    def archive_files(cls, root: Path) -> list[Path]:
        seen, out = set(), []
        for pat in cls.patterns:
            for f in sorted(root.glob(pat), key=lambda p: p.name.lower()):
                if f.is_file() and f not in seen and BACKUP_DIR not in f.parts:
                    seen.add(f)
                    out.append(f)
        return out

    @classmethod
    def _open(cls, f: Path) -> Pack | None:
        return open_pack(f, (cls.pack_type,))

    @classmethod
    def detect(cls, path: Path):
        p = Path(path)
        if not p.is_dir():
            return None
        for f in cls.archive_files(p):
            pack = cls._open(f)
            if pack is None:
                continue
            for i, e in enumerate(pack.entries):
                if pack.is_script(e):
                    return cls(p)
                if cls.pack_type is BgiArc and i > 400:
                    break
        return None

    def describe(self) -> str:
        names = [f.name for f in self.archive_files(self.root) if self._open(self.source(f)) is not None]
        return f"{self.name} ({', '.join(names[:3])}{'…' if len(names) > 3 else ''})"

    def _scripts(self) -> dict[str, tuple[Path, Pack, str]]:
        """staged name -> (archive in the game folder, opened pack, entry name); later archives win."""
        out: dict[str, tuple[Path, Pack, str]] = {}
        for f in self.archive_files(self.root):
            pack = self._open(self.source(f))
            if pack is None:
                continue
            for e in pack.entries:
                if pack.is_script(e):
                    staged = safe_name(e.name.rsplit("/", 1)[-1] if self.flatten else e.name)
                    out[staged] = (f, pack, e.name)
        return self._choose(out)

    def _choose(self, scripts: dict) -> dict:
        # VNTextPatch handles one file extension per folder: keep the most common one.
        exts = Counter(Path(k).suffix.lower() for k in scripts)
        if len(exts) > 1 and self.pack_type is not SoftpalPac:
            ext = exts.most_common(1)[0][0]
            scripts = {k: v for k, v in scripts.items() if Path(k).suffix.lower() == ext}
        return scripts

    # -- VNTPEngine hooks ---------------------------------------------------------------
    def stage_scripts(self, dest: Path) -> None:
        mapping = {}
        for staged, (f, pack, name) in self._scripts().items():
            target = dest / staged
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(pack.read(pack._by_name(name)))
            mapping[staged] = [f.relative_to(self.root).as_posix(), name]
        if not mapping:
            raise RuntimeError("No script files found inside the game's archives.")
        (self.work / "pack_map.json").write_text(json.dumps(mapping, ensure_ascii=False, indent=1), "utf-8")

    def install(self, patched: Path, sjis_ext: Path, progress: Progress) -> list[str]:
        mapping = json.loads((self.work / "pack_map.json").read_text("utf-8"))
        per_archive: dict[str, dict[str, bytes]] = {}
        orig = self.work / "orig"
        for staged, (arc_rel, name) in mapping.items():
            new = patched / staged
            if not new.is_file():
                continue
            data = new.read_bytes()
            old = orig / staged
            if old.is_file() and old.read_bytes() == data:
                continue
            per_archive.setdefault(arc_rel, {})[name] = data
        written = []
        for i, (arc_rel, repl) in enumerate(per_archive.items()):
            arc = self.root / arc_rel
            self.backup(arc)
            pack = self._open(self.source(arc))
            if pack is None:
                raise RuntimeError(f"Cannot reopen {arc_rel}")
            tmp = arc.with_name(arc.name + ".vnarabizer-tmp")
            try:
                pack.write(tmp, repl, lambda f, i=i: progress(f"Rebuilding {arc.name}", 0.7 + 0.25 * (i + f) / len(per_archive)))
                tmp.replace(arc)
            finally:
                tmp.unlink(missing_ok=True)
            if getattr(pack, "checksum_kept", False):
                self.warnings.append(f"{arc.name}: unknown checksum type, checksums of changed files were left as they were.")
            written.append(arc.name)
        if not written:
            self.warnings.append("No script changed (nothing translated yet?).")
        added = self.deploy_runtime(tool.find_game_exe(self.root), sjis_ext)
        return written + [a.name for a in added]


class BGIEngine(PackedEngine):
    name = "BGI / Ethornell"
    pack_type = BgiArc
    patterns = ("*.arc",)
    fmt = "ethornell"
    line_em = 26.0


class MajiroEngine(PackedEngine):
    name = "Majiro"
    pack_type = MajiroArc
    patterns = ("*.arc",)
    line_em = 26.0


class AdvHDEngine(PackedEngine):
    name = "AdvHD (WillPlus)"
    pack_type = AdvHdArc
    patterns = ("*.arc",)
    line_em = 25.0


class ArtemisEngine(PackedEngine):
    name = "Artemis"
    pack_type = PfsArc
    patterns = ("*.pfs", "*.pfs.[0-9][0-9][0-9]")
    line_em = 26.0

    def install(self, patched, sjis_ext, progress):
        out = super().install(patched, sjis_ext, progress)
        self.warnings.append(
            "Artemis draws text with its own font file: if Arabic letters show as boxes, "
            "replace the font in the game's font folder with “VN Arabic.ttf”."
        )
        return out


class YurisEngine(PackedEngine):
    name = "YU-RIS"
    pack_type = YpfArc
    patterns = ("*.ypf", "pac/*.ypf")
    flatten = True
    line_em = 26.0


class SoftpalEngine(PackedEngine):
    name = "Softpal"
    pack_type = SoftpalPac
    patterns = ("*.pac",)
    line_em = 26.0

    def _choose(self, scripts):
        names = {k.upper() for k in scripts}
        if not {"SCRIPT.SRC", "TEXT.DAT", "POINT.DAT"} <= names:
            return {}
        return scripts


class KaguyaEngine(VNTPEngine):
    """Atelier Kaguya: message scripts are loose .dat files starting with [SCR-MESSAGE]."""

    name = "Kaguya"
    line_em = 26.0
    MAGIC = b"[SCR-MESSAGE]"

    @classmethod
    def _files(cls, root: Path) -> list[Path]:
        out = []
        for f in list(root.glob("*.dat")) + list(root.glob("*/*.dat")):
            if BACKUP_DIR in f.parts or not f.is_file():
                continue
            try:
                with open(f, "rb") as h:
                    if h.read(len(cls.MAGIC)) == cls.MAGIC:
                        out.append(f)
            except OSError:
                pass
        return sorted(out)

    @classmethod
    def detect(cls, path: Path):
        p = Path(path)
        return cls(p) if p.is_dir() and cls._files(p) else None

    def describe(self) -> str:
        return "Kaguya ([SCR-MESSAGE] .dat)"

    def stage_scripts(self, dest: Path) -> None:
        for f in self._files(self.root):
            target = dest / f.relative_to(self.root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.source(f), target)

    def install(self, patched: Path, sjis_ext: Path, progress: Progress) -> list[str]:
        written = []
        for p in patched.rglob("*.dat"):
            target = self.root / p.relative_to(patched)
            self.backup(target)
            shutil.copy2(p, target)
            written.append(target.name)
        added = self.deploy_runtime(tool.find_game_exe(self.root), sjis_ext)
        return written + [a.name for a in added]


PACKED_ENGINES = (BGIEngine, MajiroEngine, AdvHDEngine, ArtemisEngine, YurisEngine, SoftpalEngine, KaguyaEngine)

__all__ = ["PACKED_ENGINES", "PackError", *[c.__name__ for c in PACKED_ENGINES]]
