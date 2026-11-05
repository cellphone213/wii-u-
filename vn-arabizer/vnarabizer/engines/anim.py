"""Anim / Crowd (SIS Planning): ``*_sce.dat`` scripts and ``*_define.dat`` definitions.

Translatable text is every string in the script's string table that starts
with a non-ASCII character (dialogue, narration, speaker names, chapter
titles). Strings that start with an ASCII character are resource names
(``BG020``, ``w000001d``, ``Mmiyabi030``...) and are never touched. In
``*_define.dat`` the inline strings with Japanese text are translated (for
example the recollection-room descriptions).

Inside the text, ``@n`` is a line break and ``漢字＜よみ＞`` is furigana; the
reading is dropped before translating.

The engine is Shift-JIS only and draws text left to right: Arabic is
written shaped and in visual order with VNTextPatch-compatible Shift-JIS
tunnelling, and VNTextProxy (next to the game's exe, with ``sjis_ext.bin``
and the Arabic font) draws it.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..archives.anim import AnimError, DefineFile, SceScript
from ..core import arabic_visual
from ..core.protect import SYNTAXES, untranslatable_reason
from ..core.sjis_tunnel import SjisTunnel
from .base import Entry, Progress, _noop
from .vntp import tool
from .vntp.base import BACKUP_DIR, VNTPEngine

TOKENS = SYNTAXES["anim"]
_RUBY = re.compile(r"＜[^＜＞\n]*＞")
NEWLINE = "@n"


def source_text(raw: bytes) -> str | None:
    """The text to translate for a string-table entry, or None if it is not text."""
    if not raw or raw[0] < 0x80:
        return None  # empty, or a resource name / code
    try:
        text = raw.decode("cp932")
    except UnicodeDecodeError:
        return None
    if any(ord(c) < 0x20 and c != "\t" for c in text):
        return None
    return _RUBY.sub("", text)


def _files(root: Path) -> tuple[list[Path], list[Path]]:
    def find(suffix: str) -> list[Path]:
        out = []
        for f in list(root.glob("*")) + list(root.glob("*/*")):
            if f.is_file() and f.name.lower().endswith(suffix) and BACKUP_DIR not in f.parts:
                out.append(f)
        return sorted(out, key=lambda p: p.as_posix().lower())

    return find("_sce.dat"), find("_define.dat")


class AnimEngine(VNTPEngine):
    name = "Anim / Crowd (SIS Planning)"
    markup = "anim"
    line_em = 23.0

    @classmethod
    def detect(cls, path: Path) -> "AnimEngine | None":
        p = Path(path)
        if not p.is_dir():
            return None
        sce, _ = _files(p)
        for f in sce:
            try:
                SceScript.load(f.read_bytes())
                return cls(p)
            except (AnimError, OSError, ValueError):
                continue
        return None

    def describe(self) -> str:
        sce, define = _files(self.root)
        return f"{self.name} ({', '.join(f.name for f in (sce + define)[:3])})"

    def stage_scripts(self, dest: Path) -> None:  # pragma: no cover - not used
        raise NotImplementedError

    def install(self, patched: Path, sjis_ext: Path, progress: Progress) -> list[str]:  # pragma: no cover
        raise NotImplementedError

    # -- extraction ------------------------------------------------------------------------
    def extract(self, progress: Progress = _noop) -> list[Entry]:
        self.warnings = []
        entries: dict[tuple[str, str], Entry] = {}

        def add(text: str, where: str) -> None:
            key = ("line", text)
            if key in entries:
                entries[key].count += 1
                return
            reason = untranslatable_reason(text, "anim")
            if reason not in ("empty", "markup only", "numbers/symbols only"):
                reason = None
            entries[key] = Entry("line", text, "dialogue", "", where, 0, order=len(entries) + 1, skip_reason=reason)

        sce, define = _files(self.root)
        for n, f in enumerate(sce + define):
            progress(f"Reading {f.name}", n / max(1, len(sce) + len(define)))
            raw = self.source(f).read_bytes()
            try:
                if f in sce:
                    script = SceScript.load(raw)
                    for i, s in enumerate(script.strings):
                        text = source_text(s)
                        if text is not None:
                            add(text, f"{f.name}#{i}")
                else:
                    for i, text in enumerate(DefineFile.load(raw).texts()):
                        add(_RUBY.sub("", text), f"{f.name}#{i}")
            except AnimError as e:
                self.warnings.append(f"{f.name}: {e}")
        if not entries:
            raise RuntimeError("No text found in the *_sce.dat / *_define.dat files.")
        progress("Done", 1.0)
        return sorted(entries.values(), key=lambda e: e.order)

    # -- patching --------------------------------------------------------------------------
    def _visual(self, tr: str) -> str:
        # The translation keeps the original "@n" breaks as tokens; turn them into
        # paragraph breaks, then wrap for the window and join lines with "@n" again.
        text = tr.replace(NEWLINE, "\n")
        return arabic_visual.visual_text(text, self.line_em, TOKENS, newline=NEWLINE)

    def apply(self, translations: dict[tuple[str, str], str], progress: Progress = _noop) -> list[str]:
        self.warnings = []
        tunnel = SjisTunnel()
        written: list[str] = []
        sce, define = _files(self.root)
        files = sce + define

        def encode(text: str) -> bytes:
            b = tunnel.encode(text)
            if b"\0" in b:
                raise AnimError("encoded text contains a 0x00 byte")
            return b

        for n, f in enumerate(files):
            progress(f"Writing {f.name}", n / max(1, len(files)))
            raw = self.source(f).read_bytes()
            changed = 0
            try:
                if f in sce:
                    script = SceScript.load(raw)
                    for i, s in enumerate(script.strings):
                        text = source_text(s)
                        tr = translations.get(("line", text)) if text is not None else None
                        if tr:
                            script.strings[i] = encode(self._visual(tr))
                            changed += 1
                    data = script.dump() if changed else None
                else:
                    df = DefineFile.load(raw)
                    for i, p in enumerate(df.pieces):
                        if isinstance(p, str):
                            tr = translations.get(("line", _RUBY.sub("", p)))
                            if tr:
                                df.pieces[i] = self._visual(tr)
                                changed += 1
                    data = df.dump(encode) if changed else None
            except AnimError as e:
                self.warnings.append(f"{f.name}: {e}")
                continue
            if data is None:
                continue
            self.backup(f)
            f.write_bytes(data)
            written.append(f.relative_to(self.root).as_posix())

        sjis_ext = self.work / "sjis_ext.bin"
        tunnel.save(sjis_ext)
        added = self.deploy_runtime(tool.find_game_exe(self.root), sjis_ext)
        progress("Done", 1.0)
        return written + [a.name for a in added]


__all__ = ["AnimEngine", "source_text"]
