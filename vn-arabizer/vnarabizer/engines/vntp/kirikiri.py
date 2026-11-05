"""KiriKiri 2 / KiriKiri Z (KAG3, and .scn PSB scenarios used with E-mote).

Scenario files (.ks, .scn) are read from the game's unencrypted .xp3
archives. The translation goes into a new ``patchN.xp3`` that the engine
loads on top of the originals, together with KirikiriTools'
KirikiriUnencryptedArchive (version.dll) so the unencrypted patch is also
accepted by games with encrypted archives. KiriKiri stores text as UTF-16,
so Arabic needs no Shift-JIS workaround; VNTextProxy supplies the font.

Encrypted archives (per-game schemes) cannot be unpacked here: extract the
scenario files with GARbro and open the extracted folder instead.
"""

from __future__ import annotations

import re
from pathlib import Path

from ...archives.xp3 import Xp3Archive, Xp3Error, write_xp3
from ..base import Progress
from . import tool
from .base import VNTPEngine, safe_name

SCRIPT_EXT = (".ks", ".scn")


def _looks_like_script(name: str, data: bytes) -> bool:
    if name.lower().endswith(".scn"):
        return data[:4] in (b"PSB\x00", b"mdf\x00")
    if data[:2] in (b"\xff\xfe", b"\xfe\xff") or data[:3] == b"\xef\xbb\xbf":
        return True
    if data[:5] in (b"\xfe\xfe\x00\xff\xfe", b"\xfe\xfe\x01\xff\xfe", b"\xfe\xfe\x02\xff\xfe"):
        return False  # scrambled: needs KirikiriDescrambler
    try:
        data[:4096].decode("cp932")
        return True
    except UnicodeDecodeError:
        return False


class KiriKiriEngine(VNTPEngine):
    name = "KiriKiri"
    line_em = 24.0
    avoid_proxies = ("version",)  # version.dll is KirikiriUnencryptedArchive

    @classmethod
    def detect(cls, path: Path) -> "KiriKiriEngine | None":
        p = Path(path)
        if (p / "data.xp3").is_file() or any(p.glob("*.xp3")):
            return cls(p)
        return None

    def describe(self) -> str:
        return "KiriKiri / KAG"

    def _archives(self) -> list[Path]:
        """Game archives in load order, excluding patches we created."""
        own = set(self._manifest().get("added", []))
        arcs = [self.root / "data.xp3"] if (self.root / "data.xp3").exists() else []
        arcs += sorted(p for p in self.root.glob("*.xp3") if p.name.lower() != "data.xp3" and not p.name.lower().startswith("patch"))
        n = 1
        while True:
            p = self.root / ("patch.xp3" if n == 1 else f"patch{n}.xp3")
            if not p.exists():
                break
            if p.relative_to(self.root).as_posix() not in own:
                arcs.append(p)
            n += 1
        return arcs

    def stage_scripts(self, dest: Path) -> None:
        found: dict[str, bytes] = {}
        problems = []
        for arc_path in self._archives():
            try:
                arc = Xp3Archive(arc_path)
            except Xp3Error as e:
                problems.append(f"{arc_path.name}: {e}")
                continue
            for e in arc.entries:
                base = e.name.replace("\\", "/").split("/")[-1]
                if not base.lower().endswith(SCRIPT_EXT):
                    continue
                if e.encrypted:
                    problems.append(f"{arc_path.name}: {base} is encrypted")
                    continue
                try:
                    data = arc.read(e)
                except Exception as err:
                    problems.append(f"{arc_path.name}: {base}: {err}")
                    continue
                if _looks_like_script(base, data):
                    found[base] = data  # later archives override earlier ones
                else:
                    problems.append(f"{arc_path.name}: {base} is encrypted or scrambled")
        for name, data in found.items():
            (dest / safe_name(name)).write_bytes(data)
        if not found:
            raise RuntimeError(
                "No readable KiriKiri scenario files. This game's archives are probably encrypted: "
                "extract the .ks/.scn files with GARbro (or KirikiriTools), then open the extracted folder in VN Arabizer.\n"
                + "\n".join(problems[:5])
            )
        self.warnings += problems[:20]

    def _patch_name(self) -> Path:
        own = set(self._manifest().get("added", []))
        n = 1
        while True:
            p = self.root / ("patch.xp3" if n == 1 else f"patch{n}.xp3")
            if not p.exists() or p.relative_to(self.root).as_posix() in own:
                return p
            n += 1

    def install(self, patched: Path, sjis_ext: Path, progress: Progress) -> list[str]:
        files = [(p.name, p.read_bytes()) for p in sorted(patched.rglob("*")) if p.is_file() and p.suffix.lower() in SCRIPT_EXT]
        patch = self._patch_name()
        write_xp3(patch, files)
        added = [patch]
        exe = tool.find_game_exe(self.root)
        if exe is not None and "version" in tool.exe_imports(exe):
            dst = self.root / "version.dll"
            if dst.exists():
                self.backup(dst)
            dst.write_bytes(tool.kirikiri_unencrypted_dll().read_bytes())
            added.append(dst)
        elif exe is not None:
            self.warnings.append(
                f"{exe.name} does not import version.dll; if the game's archives are encrypted it may ignore {patch.name}."
            )
        self._record_added(added)
        added += self.deploy_runtime(exe, sjis_ext)
        return [a.name for a in added]
