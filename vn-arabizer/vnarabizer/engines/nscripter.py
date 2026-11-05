"""NScripter (nscript.dat or 0.txt/00.txt scripts).

``nscript.dat`` is the plain-text script with every byte XORed with 0x84
(the usual NScripter obfuscation). Text lines start with a non-ASCII character (or a colour /
wait code); everything that starts with an ASCII word is a command and is
left alone, except the visible strings of choice commands such as
``select "text",*label``.

NScripter is Shift-JIS only: Arabic is written with VNTextPatch-compatible
"SJIS tunneling" and shown by VNTextProxy with the Arabic font.
ONScripter (the open-source re-implementation) renders text itself and
cannot be used this way.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..core import arabic_visual
from ..core.protect import SYNTAXES, untranslatable_reason
from ..core.sjis_tunnel import SjisTunnel
from .base import Entry, Progress, _noop
from .vntp import tool
from .vntp.base import VNTPEngine

TOKENS = SYNTAXES["nscr"]
CHOICE_CMDS = {"select", "selgosub", "selnum", "csel", "caption", "versionstr", "mesbox", "yesnobox", "okcancelbox", "textbtnwait"}
_QUOTED = re.compile(r'"([^"\r\n]*)"')
_NON_ASCII = re.compile(r"[^\x00-\x7f]")
_COMMAND = re.compile(r"^[ \t]*([A-Za-z_][A-Za-z0-9_]*)")


def _script_files(root: Path) -> list[Path]:
    if (root / "nscript.dat").is_file():
        return [root / "nscript.dat"]
    numbered = []
    for i in range(100):
        for name in (f"{i}.txt", f"{i:02d}.txt"):
            if (root / name).is_file() and root / name not in numbered:
                numbered.append(root / name)
    return numbered


def _decode(path: Path, data: bytes) -> bytes:
    return bytes(b ^ 0x84 for b in data) if path.name.lower() == "nscript.dat" else data


def is_onscripter(root: Path) -> bool:
    exes = [p.name.lower() for p in root.glob("*.exe")]
    return any("onscripter" in e or e.startswith("ons") for e in exes) or (root / "default.ttf").exists()


def classify(line: str) -> str:
    """'text', 'choice', or 'code' for one script line."""
    s = line.strip()
    if not s or s[0] in ";*~":
        return "code"
    m = _COMMAND.match(line)
    if m:
        return "choice" if m.group(1).lower() in CHOICE_CMDS and _NON_ASCII.search(s) else "code"
    if s.startswith('"'):
        return "choice" if _NON_ASCII.search(s) else "code"  # continuation line of a select
    if s[0] == "`":
        return "code"  # one-byte (English) text mode: not handled
    return "text" if _NON_ASCII.search(s) else "code"


class NScripterEngine(VNTPEngine):
    name = "NScripter"
    markup = "nscr"
    line_em = 22.0

    @classmethod
    def detect(cls, path: Path) -> "NScripterEngine | None":
        p = Path(path)
        return cls(p) if _script_files(p) else None

    def describe(self) -> str:
        files = _script_files(self.root)
        kind = "ONScripter" if is_onscripter(self.root) else "NScripter"
        return f"{kind} ({', '.join(f.name for f in files[:3])}{'…' if len(files) > 3 else ''})"

    # VNTPEngine hooks are not used: NScripter scripts are handled here directly.
    def stage_scripts(self, dest: Path) -> None:  # pragma: no cover
        raise NotImplementedError

    def install(self, patched: Path, sjis_ext: Path, progress: Progress) -> list[str]:  # pragma: no cover
        raise NotImplementedError

    def _lines(self):
        """(file, index, raw line bytes, decoded text or None) for every script line."""
        for f in _script_files(self.root):
            data = _decode(f, self.source(f).read_bytes())
            for i, raw in enumerate(data.split(b"\n")):
                try:
                    text = raw.rstrip(b"\r").decode("cp932")
                except UnicodeDecodeError:
                    text = None
                yield f, i, raw, text

    def extract(self, progress: Progress = _noop) -> list[Entry]:
        self.warnings = []
        if is_onscripter(self.root):
            self.warnings.append(
                "This looks like ONScripter: it draws text itself, so Arabic letters will not show even with VNTextProxy."
            )
        entries: dict[tuple[str, str], Entry] = {}
        order = 0

        def add(channel, kind, text, where):
            nonlocal order
            key = (channel, text)
            if key in entries:
                entries[key].count += 1
                return
            order += 1
            reason = untranslatable_reason(text, "nscr")
            if kind == "dialogue" and reason not in ("empty", "markup only", "numbers/symbols only"):
                reason = None
            entries[key] = Entry(channel, text, kind, "", where, 0, order=order, skip_reason=reason)

        for f, i, raw, text in self._lines():
            if text is None:
                continue
            kind = classify(text)
            if kind == "text":
                add("line", "dialogue", text.strip(), f"{f.name}:{i + 1}")
            elif kind == "choice":
                for m in _QUOTED.finditer(text):
                    if _NON_ASCII.search(m.group(1)):
                        add("choice", "menu", m.group(1), f"{f.name}:{i + 1}")
        if not entries:
            raise RuntimeError("No text found in the NScripter script.")
        progress("Done", 1.0)
        return sorted(entries.values(), key=lambda e: e.order)

    def apply(self, translations: dict[tuple[str, str], str], progress: Progress = _noop) -> list[str]:
        self.warnings = []
        tunnel = SjisTunnel()
        written = []
        files = _script_files(self.root)
        for fi, f in enumerate(files):
            data = _decode(f, self.source(f).read_bytes())
            out_lines: list[bytes] = []
            changed = False
            for raw in data.split(b"\n"):
                cr = raw.endswith(b"\r")
                try:
                    text = raw.rstrip(b"\r").decode("cp932")
                except UnicodeDecodeError:
                    out_lines.append(raw)
                    continue
                kind = classify(text)
                new = None
                if kind == "text":
                    tr = translations.get(("line", text.strip()))
                    if tr:
                        indent = text[: len(text) - len(text.lstrip())]
                        lines = arabic_visual.visual_text(tr, self.line_em, TOKENS, newline="\n").split("\n")
                        new = [indent + l for l in lines]
                elif kind == "choice":
                    def repl(m):
                        tr = translations.get(("choice", m.group(1)))
                        if not tr:
                            return m.group(0)
                        return '"' + arabic_visual.visual_line(tr, TOKENS).replace('"', "”") + '"'

                    replaced = _QUOTED.sub(repl, text)
                    if replaced != text:
                        new = [replaced]
                if new is None:
                    out_lines.append(raw)
                    continue
                changed = True
                end = b"\r" if cr else b""
                out_lines.extend(tunnel.encode(l) + end for l in new)
            if not changed:
                continue
            body = b"\n".join(out_lines)
            self.backup(f)
            f.write_bytes(_decode(f, body))  # XOR is its own inverse
            written.append(f.name)
            progress(f"Wrote {f.name}", (fi + 1) / len(files))

        sjis_ext = self.work / "sjis_ext.bin"
        tunnel.save(sjis_ext)
        added = self.deploy_runtime(tool.find_game_exe(self.root), sjis_ext)
        if is_onscripter(self.root):
            self.warnings.append("ONScripter draws text itself: Arabic will not show. The original NScripter engine is needed.")
        return written + [a.name for a in added]
