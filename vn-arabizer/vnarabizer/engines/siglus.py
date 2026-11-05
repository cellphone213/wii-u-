"""SiglusEngine (Scene.pck + Gameexe.dat): Key/Visual Arts and many others.

Most Siglus games encrypt scenes with a 16-byte key unique to the game.
It is taken, in this order, from:
  1. ``siglus_key.txt`` in the game folder (16 hex bytes, e.g. from SiglusExtract),
  2. ``key.toml`` in the game folder (written by SiglusExtract / siglus_rs),
  3. the bundled siglus-static-key-tool (xmoezzz, MPL-2.0), which recovers
     it from Scene.pck + Gameexe.dat without running the game.

Siglus stores text as UTF-16, so Arabic needs no Shift-JIS workaround;
VNTextProxy supplies the Arabic font.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from ..archives.siglus import ScenePck, SiglusError, parse_key
from ..core import arabic_visual
from ..core.protect import SYNTAXES, untranslatable_reason
from .base import Entry, Progress, _noop
from .vntp import tool
from .vntp.base import VNTPEngine

_JAPANESE = re.compile(r"[぀-ヿ㐀-鿿ｦ-ﾟ]")
TOKENS = SYNTAXES["vntp"]


def _key_tool() -> Path:
    exe = tool.tools_dir() / "SiglusKey" / "siglus-static-key-tool.exe"
    if not exe.exists():
        raise tool.ToolMissing("siglus-static-key-tool is not bundled with this build.")
    return exe


class SiglusEngine(VNTPEngine):
    name = "Siglus"
    markup = "vntp"
    line_em = 24.0

    @classmethod
    def detect(cls, path: Path) -> "SiglusEngine | None":
        p = Path(path)
        scene = next((f for f in p.glob("*") if f.name.lower() == "scene.pck"), None)
        return cls(p) if scene is not None else None

    def describe(self) -> str:
        return "SiglusEngine"

    def stage_scripts(self, dest: Path) -> None:  # pragma: no cover - not used
        raise NotImplementedError

    def install(self, patched: Path, sjis_ext: Path, progress: Progress) -> list[str]:  # pragma: no cover
        raise NotImplementedError

    @property
    def scene_path(self) -> Path:
        return next(f for f in self.root.glob("*") if f.name.lower() == "scene.pck")

    @property
    def gameexe_path(self) -> Path | None:
        return next((f for f in self.root.glob("*") if f.name.lower() == "gameexe.dat"), None)

    # -- key -------------------------------------------------------------------------
    def _key_from_files(self) -> bytes | None:
        for name in ("siglus_key.txt", "key.toml"):
            for base in (self.root, self.work):
                f = base / name
                if f.is_file():
                    k = parse_key(f.read_text("utf-8", "replace"))
                    if k:
                        return k
        return None

    def _key_from_tool(self) -> bytes | None:
        exe = _key_tool()
        scene = str(self.source(self.scene_path))
        attempts = []
        if self.gameexe_path is not None:
            attempts.append(["--scene", scene, "--game", str(self.gameexe_path)])
        game_exe = tool.find_game_exe(self.root)
        if game_exe is not None:
            attempts.append(["--exe", str(game_exe), "--scene", scene])
        flags = 0x08000000 if sys.platform == "win32" else 0
        for args in attempts:
            try:
                p = subprocess.run([str(exe), *args], capture_output=True, text=True, timeout=1800, creationflags=flags)
            except Exception as e:
                self.warnings.append(f"key tool: {e}")
                continue
            m = re.search(r"^key\s*:\s*((?:[0-9A-Fa-f]{2}\s*){16})", p.stdout, re.M)
            if m:
                key = parse_key(m.group(1))
                (self.work / "siglus_key.txt").write_text(m.group(1).strip(), "utf-8")
                return key
            self.warnings.append("key tool: " + (p.stdout + p.stderr).strip().splitlines()[-1:][0] if (p.stdout + p.stderr).strip() else "key tool found no key")
        return None

    def _open(self) -> ScenePck:
        data = self.source(self.scene_path).read_bytes()
        try:
            return ScenePck(data)
        except SiglusError as e:
            if "per-game key" not in str(e):
                raise RuntimeError(f"Scene.pck: {e}") from e
        key = self._key_from_files()
        if key is None:
            try:
                key = self._key_from_tool()
            except tool.ToolMissing as e:
                raise RuntimeError(str(e)) from e
        if key is None:
            raise RuntimeError(
                "Scene.pck is encrypted with this game's key and the key could not be found. "
                "Put it (16 hex bytes) in a file named siglus_key.txt in the game folder."
            )
        try:
            return ScenePck(data, key)
        except SiglusError as e:
            raise RuntimeError(f"Scene.pck: {e}") from e

    # -- extraction / patching -----------------------------------------------------------
    def extract(self, progress: Progress = _noop) -> list[Entry]:
        self.warnings = []
        progress("Opening Scene.pck", 0.1)
        pck = self._open()
        entries: dict[tuple[str, str], Entry] = {}
        order = 0
        for si, scene in enumerate(pck.scenes):
            for text in scene.strings:
                if not _JAPANESE.search(text):
                    continue
                key = ("line", text)
                if key in entries:
                    entries[key].count += 1
                    continue
                order += 1
                reason = untranslatable_reason(text, "vntp")
                if reason not in ("empty", "markup only", "numbers/symbols only"):
                    reason = None
                entries[key] = Entry("line", text, "dialogue", "", scene.name, 0, order=order, skip_reason=reason)
            progress(f"Reading {scene.name}", 0.1 + 0.9 * si / max(1, len(pck.scenes)))
        if not entries:
            raise RuntimeError("No Japanese text found in Scene.pck.")
        return sorted(entries.values(), key=lambda e: e.order)

    def apply(self, translations: dict[tuple[str, str], str], progress: Progress = _noop) -> list[str]:
        self.warnings = []
        pck = self._open()
        changes: dict[int, list[str]] = {}
        for si, scene in enumerate(pck.scenes):
            new = []
            changed = False
            for text in scene.strings:
                tr = translations.get(("line", text))
                if tr:
                    new.append(arabic_visual.visual_text(tr, self.line_em, TOKENS, newline="\n"))
                    changed = True
                else:
                    new.append(text)
            if changed:
                changes[si] = new
        progress("Rebuilding Scene.pck", 0.6)
        data = pck.rebuild(changes)
        self.backup(self.scene_path)
        self.scene_path.write_bytes(data)
        added = self.deploy_runtime(tool.find_game_exe(self.root), self.work / "none.bin")
        progress("Done", 1.0)
        return [self.scene_path.name] + [a.name for a in added]
