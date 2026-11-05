"""Shared logic for engines handled through VNTextPatch.

Flow:
  stage_scripts()  original script files -> work/orig
  VNTextPatch extractlocal  -> work/json      (one JSON per script)
  translate (VN Arabizer pipeline)
  Arabic is prepared for left-to-right engines (wrapped, joined, reordered)
  VNTextPatch insertlocal   -> work/patched  (+ sjis_ext.bin)
  install()        patched scripts back into the game, plus VNTextProxy
                   and the Arabic font next to the game executable.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from abc import abstractmethod
from pathlib import Path

from ...core import arabic_visual
from ...core.paths import assets_dir, data_dir
from ...core.protect import SYNTAXES, untranslatable_reason
from ..base import Engine, Entry, Progress, _noop
from . import tool

BACKUP_DIR = "vnarabizer_backup"
FONT_FILE = "VN Arabic.ttf"  # VNTextProxy derives the font name from the file name
TOKENS = SYNTAXES["vntp"]


class VNTPEngine(Engine):
    markup = "vntp"
    #: VNTextPatch --format value when the format has no unique extension.
    fmt: str | None = None
    #: Message line width in ems (≈ full-width Japanese characters per line).
    line_em: float = 26.0
    #: DLL names VNTextProxy must not use for this engine.
    avoid_proxies: tuple[str, ...] = ()

    def __init__(self, root: Path):
        super().__init__(root)
        self.warnings: list[str] = []

    # -- folders ------------------------------------------------------------------
    @property
    def work(self) -> Path:
        key = hashlib.sha1(str(self.root.resolve()).lower().encode()).hexdigest()[:16]
        p = data_dir() / "work" / f"{self.root.name[:30]}-{key}"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def backup_path(self, f: Path) -> Path:
        return self.root / BACKUP_DIR / f.relative_to(self.root)

    def backup(self, f: Path) -> None:
        b = self.backup_path(f)
        if f.exists() and not b.exists():
            b.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, b)

    def source(self, f: Path) -> Path:
        b = self.backup_path(f)
        return b if b.exists() else f

    @property
    def manifest_path(self) -> Path:
        return self.root / BACKUP_DIR / "manifest.json"

    def _manifest(self) -> dict:
        try:
            return json.loads(self.manifest_path.read_text("utf-8"))
        except Exception:
            return {"added": []}

    def _record_added(self, files: list[Path]) -> None:
        m = self._manifest()
        for f in files:
            rel = f.relative_to(self.root).as_posix() if f.is_relative_to(self.root) else str(f)
            if rel not in m["added"]:
                m["added"].append(rel)
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(json.dumps(m, indent=1), "utf-8")

    # -- engine specific ------------------------------------------------------------
    @abstractmethod
    def stage_scripts(self, dest: Path) -> None:
        """Copy the original script files into ``dest``."""

    @abstractmethod
    def install(self, patched: Path, sjis_ext: Path, progress: Progress) -> list[str]:
        """Put patched scripts into the game. Returns files written."""

    # -- extraction -------------------------------------------------------------------
    def extract(self, progress: Progress = _noop) -> list[Entry]:
        self.warnings = []
        orig, js = self.work / "orig", self.work / "json"
        for d in (orig, js):
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True)
        progress("Unpacking scripts", 0.1)
        self.stage_scripts(orig)
        if not any(p.is_file() for p in orig.rglob("*")):
            raise RuntimeError("No script files found.")
        progress("Reading scripts with VNTextPatch", 0.4)
        tool.extract(orig, js, self.fmt)
        data = tool.read_json_dir(js)
        if not data:
            raise RuntimeError("VNTextPatch found no text in these scripts.")
        entries: dict[tuple[str, str], Entry] = {}
        order = 0

        def add(channel, kind, text, where, speaker=""):
            nonlocal order
            if not isinstance(text, str) or not text.strip():
                return
            key = (channel, text)
            if key in entries:
                entries[key].count += 1
                return
            order += 1
            reason = untranslatable_reason(text, "vntp")
            if kind == "dialogue" and reason not in ("empty", "markup only", "numbers/symbols only"):
                reason = None
            entries[key] = Entry(channel, text, kind, speaker, where, 0, order=order, skip_reason=reason)

        for fname, items in data.items():
            for i, item in enumerate(items):
                names = item.get("names") or ([item["name"]] if item.get("name") else [])
                for n in names:
                    add("name", "name", n, fname)
                add("message", "dialogue", item.get("message", ""), f"{fname}#{i + 1}", names[0] if names else "")
        progress("Done", 1.0)
        return sorted(entries.values(), key=lambda e: e.order)

    # -- patching ---------------------------------------------------------------------
    def _visual(self, text: str, multiline: bool) -> str:
        if multiline:
            return arabic_visual.visual_text(text, self.line_em, TOKENS, newline="\r\n")
        return arabic_visual.visual_line(text, TOKENS)

    def apply(self, translations: dict[tuple[str, str], str], progress: Progress = _noop) -> list[str]:
        self.warnings = []
        orig, js = self.work / "orig", self.work / "json"
        if not js.is_dir() or not any(js.rglob("*.json")):
            self.extract(progress)
        tjs, patched = self.work / "translated", self.work / "patched"
        for d in (tjs, patched):
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True)

        progress("Preparing Arabic text", 0.1)
        for fname, items in tool.read_json_dir(js).items():
            out = []
            for item in items:
                new = dict(item)
                if item.get("name"):
                    tr = translations.get(("name", item["name"]))
                    new["name"] = self._visual(tr, False) if tr else item["name"]
                if item.get("names"):
                    new["names"] = [self._visual(translations[("name", n)], False) if translations.get(("name", n)) else n for n in item["names"]]
                msg = item.get("message", "")
                tr = translations.get(("message", msg))
                new["message"] = self._visual(tr, True) if tr else msg
                out.append(new)
            target = tjs / fname
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")

        progress("Writing scripts with VNTextPatch", 0.4)
        sjis_ext = self.work / "sjis_ext.bin"
        sjis_ext.unlink(missing_ok=True)
        tool.insert(orig, tjs, patched, sjis_ext, self.fmt)
        progress("Installing into the game", 0.7)
        written = self.install(patched, sjis_ext, progress)
        progress("Done", 1.0)
        return written

    # -- runtime (VNTextProxy + font) ----------------------------------------------------
    def deploy_runtime(self, exe: Path | None, sjis_ext: Path, extra_avoid: tuple[str, ...] = ()) -> list[Path]:
        """Copy the Arabic font, VNTextProxy and sjis_ext.bin next to the game executable."""
        if exe is None:
            self.warnings.append("Game executable not found: copy VNTextProxy and the font next to it yourself.")
            return []
        folder = exe.parent
        added: list[Path] = []
        if not tool.is_32bit(exe):
            self.warnings.append(f"{exe.name} is 64-bit; VNTextProxy only supports 32-bit games.")
        proxy = tool.choose_proxy(exe, self.avoid_proxies + extra_avoid)
        if proxy is None:
            self.warnings.append(
                f"{exe.name} imports none of {', '.join(tool.PROXIES)}; VNTextProxy cannot be loaded automatically. "
                "Arabic letters may not show."
            )
        else:
            dst = folder / f"{proxy}.dll"
            if dst.exists() and not self.backup_path(dst).exists() and str(dst.relative_to(self.root)) not in self._manifest()["added"]:
                self.backup(dst)
            shutil.copy2(tool.proxy_dll(proxy), dst)
            added.append(dst)
        other_fonts = [p.name for p in folder.glob("*.tt[fc]") if p.name != FONT_FILE] + [p.name for p in folder.glob("*.otf")]
        if other_fonts:
            self.warnings.append(
                "The game folder already contains font files (" + ", ".join(other_fonts) + "); "
                "VNTextProxy uses the first font it finds, which may not be the Arabic one."
            )
        font = folder / FONT_FILE
        shutil.copy2(assets_dir() / "fonts" / "VNArabic-Regular.ttf", font)
        shutil.copy2(assets_dir() / "fonts" / "OFL.txt", folder / "VN Arabic - OFL.txt")
        added += [font, folder / "VN Arabic - OFL.txt"]
        if sjis_ext.exists() and sjis_ext.stat().st_size:
            shutil.copy2(sjis_ext, folder / "sjis_ext.bin")
            added.append(folder / "sjis_ext.bin")
        self._record_added(added)
        return added

    def remove(self) -> list[str]:
        removed = []
        m = self._manifest()
        for rel in m.get("added", []):
            p = self.root / rel
            if p.is_file():
                p.unlink()
                removed.append(rel)
        backup = self.root / BACKUP_DIR
        if backup.is_dir():
            for b in backup.rglob("*"):
                if b.is_file() and b != self.manifest_path:
                    target = self.root / b.relative_to(backup)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(b, target)
                    removed.append(target.relative_to(self.root).as_posix())
            shutil.rmtree(backup)
        return removed

    def is_applied(self) -> bool:
        return (self.root / BACKUP_DIR).is_dir()


def safe_name(name: str) -> str:
    return re.sub(r'[<>:"|?*]', "_", name).replace("\\", "/").lstrip("/")
