"""Ren'Py engine support (Ren'Py 6.x, 7.x and 8.x)."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from ...core.paths import assets_dir
from ..base import Engine, Entry, Progress, _noop
from . import extract as ex
from .patch import DATA_DIR, PATCH_RPY, PatchOptions, main_rpy, say_json, strings_rpy
from .rpa import find_archives
from .rpyc import RpycError, read_rpyc

MANIFEST = "vnarabizer/manifest.json"


class RenPyEngine(Engine):
    name = "Ren'Py"

    def __init__(self, root: Path):
        super().__init__(root)
        self.game_dir = self.root / "game"
        self.options = PatchOptions()
        self.warnings: list[str] = []

    @classmethod
    def detect(cls, path: Path) -> "RenPyEngine | None":
        p = Path(path)
        if p.name.lower() == "game" and (p.parent / "renpy").is_dir():
            p = p.parent
        game = p / "game"
        if not game.is_dir():
            return None
        if (p / "renpy").is_dir() or any(game.glob("*.rpa")) or any(game.rglob("*.rpyc")) or any(game.rglob("*.rpy")):
            return cls(p)
        return None

    # -- info -------------------------------------------------------------------
    @property
    def version(self) -> str:
        for rel in ("renpy/vc_version.py", "renpy/__init__.py"):
            f = self.root / rel
            if not f.exists():
                continue
            text = f.read_text("utf-8", "replace")
            m = re.search(r"^version\s*=\s*['\"]([\d.]+)", text, re.M)
            if m:
                return m.group(1)
            m = re.search(r"version_tuple\s*=\s*\(([\d,\s]+)", text)
            if m:
                return ".".join(x.strip() for x in m.group(1).split(",") if x.strip())
        return ""

    @property
    def python_major(self) -> int:
        lib = self.root / "lib"
        if lib.is_dir() and any(d.name.startswith("py3") for d in lib.iterdir()):
            return 3
        v = self.version
        if v and v.split(".")[0].isdigit():
            return 3 if int(v.split(".")[0]) >= 8 else 2
        return 0

    def describe(self) -> str:
        py = {2: "Python 2", 3: "Python 3"}.get(self.python_major, "")
        parts = [p for p in (self.version, py) if p]
        return "Ren'Py" + (f" {' / '.join(parts)}" if parts else "")

    # -- extraction ---------------------------------------------------------------
    def _script_sources(self):
        """Yield (name, bytes) for every compiled script, loose files first."""
        seen: set[str] = set()
        for f in sorted(self.game_dir.rglob("*.rpyc")) + sorted(self.game_dir.rglob("*.rpymc")):
            rel = f.relative_to(self.game_dir).as_posix()
            if rel.startswith("tl/") or rel.startswith("saves/") or rel.split("/")[-1].startswith("zz_vnarabizer"):
                continue
            seen.add(rel)
            yield rel, f.read_bytes()
        for arc in find_archives(self.game_dir):
            for name in arc.names():
                if not (name.endswith(".rpyc") or name.endswith(".rpymc")):
                    continue
                if name in seen or name.startswith("tl/"):
                    continue
                seen.add(name)
                try:
                    yield name, arc.read(name)
                except Exception as e:
                    self.warnings.append(f"{arc.path.name}: {name}: {e}")

    def extract(self, progress: Progress = _noop) -> list[Entry]:
        self.warnings = []
        col = ex.Collector()
        sources = list(self._script_sources())
        if not sources:
            if any(self.game_dir.rglob("*.rpy")):
                raise RuntimeError(
                    "This game only has .rpy source files. Start the game once so Ren'Py "
                    "compiles them to .rpyc, then try again."
                )
            raise RuntimeError("No Ren'Py scripts found in this game.")
        for i, (name, data) in enumerate(sources):
            progress(f"Reading {name}", i / max(1, len(sources)))
            try:
                _, stmts = read_rpyc(data)
            except RpycError as e:
                self.warnings.append(f"{name}: {e}")
                continue
            ex.collect(stmts, name, col)

        # Interface strings from Ren'Py's own screens and messages.
        common = self.root / "renpy" / "common"
        if common.is_dir():
            for f in sorted(common.glob("*.rpyc")):
                try:
                    _, stmts = read_rpyc(f.read_bytes())
                except RpycError:
                    continue
                ex.collect(stmts, "renpy/common/" + f.name, col, ui_only=True)
        progress("Done", 1.0)
        return ex.finish(col)

    # -- patching -----------------------------------------------------------------
    def _language(self) -> str:
        tl = self.game_dir / "tl"
        lang = self.options.language
        if (tl / lang).is_dir() and not (tl / lang / "vnarabizer_strings.rpy").exists():
            # The game already ships its own translation with this name.
            lang = lang + "_vna"
        return lang

    def apply(self, translations: dict[tuple[str, str], str], progress: Progress = _noop) -> list[str]:
        self.remove()  # start clean so stale compiled files never linger
        lang = self._language()
        self.options.language = lang

        say = {orig: tr for (ch, orig), tr in translations.items() if ch == "say" and tr and tr != orig}
        strings = {orig: (tr, "") for (ch, orig), tr in translations.items() if ch == "strings" and tr and tr != orig}

        written: list[str] = []

        def write(rel: str, data: bytes) -> None:
            p = self.game_dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            written.append(rel)

        progress("Writing dialogue table", 0.2)
        write(f"{DATA_DIR}/say.json", say_json(say))
        progress("Writing interface strings", 0.4)
        write(f"tl/{lang}/vnarabizer_strings.rpy", strings_rpy(strings, lang, self.options.align_right).encode("utf-8"))
        progress("Copying Arabic font", 0.6)
        fonts = assets_dir() / "fonts"
        for name in ("VNArabic-Regular.ttf", "VNArabic-Bold.ttf", "OFL.txt"):
            src = fonts / name
            if src.exists():
                write(f"{DATA_DIR}/{name}", src.read_bytes())
            elif name.endswith(".ttf"):
                self.warnings.append(f"missing bundled font {name}")
        progress("Writing engine settings", 0.8)
        write(PATCH_RPY, main_rpy(self.options).encode("utf-8"))
        write(MANIFEST, json.dumps({"files": written + [MANIFEST], "language": lang}, indent=1).encode("utf-8"))
        progress("Done", 1.0)
        return written

    def remove(self) -> list[str]:
        removed: list[str] = []
        manifest = self.game_dir / MANIFEST
        files: list[str] = []
        lang = self.options.language
        if manifest.exists():
            try:
                data = json.loads(manifest.read_text("utf-8"))
                files = data.get("files", [])
                lang = data.get("language", lang)
            except Exception:
                pass
        files += [PATCH_RPY, f"tl/{lang}/vnarabizer_strings.rpy"]
        for rel in files:
            for candidate in (rel, rel + "c") if rel.endswith(".rpy") else (rel,):
                p = self.game_dir / candidate
                if p.is_file():
                    p.unlink()
                    removed.append(candidate)
        data_dir = self.game_dir / DATA_DIR
        if data_dir.is_dir():
            shutil.rmtree(data_dir, ignore_errors=True)
        # Remove the folders we created if they are now empty.
        for d in (self.game_dir / "tl" / lang, self.game_dir / "tl"):
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
        return removed

    def is_applied(self) -> bool:
        return (self.game_dir / PATCH_RPY).exists()
