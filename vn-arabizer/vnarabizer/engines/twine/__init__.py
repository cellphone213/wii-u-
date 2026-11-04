"""Twine 2 HTML games (SugarCube 2 and Harlowe 3).

A published Twine game is one HTML file whose passages live in
``<tw-passagedata>`` elements. Applying a translation:

1. backs up the HTML file into ``vnarabizer_backup/`` (once),
2. rewrites prose lines, link labels and link-macro labels from the
   pristine copy (link targets, i.e. passage names, are never changed),
3. injects a stylesheet with right-to-left layout and the Arabic font
   (embedded, so the single HTML file keeps working when moved).

"Remove" copies the backup back byte-for-byte.
"""

from __future__ import annotations

import base64
import html
import json
import re
import shutil
from pathlib import Path

from ...core import twine_markup as tm
from ...core.paths import assets_dir
from ...core.protect import untranslatable_reason
from ..base import Engine, Entry, Progress, _noop

BACKUP_DIR = "vnarabizer_backup"
MARKER = b"<tw-storydata"
STYLE_ID = "vnarabizer-rtl"

_STORY = re.compile(r"<tw-storydata\b[^>]*>", re.I)
_PASSAGE = re.compile(r"(<tw-passagedata\b[^>]*>)(.*?)(</tw-passagedata>)", re.S | re.I)
_ATTR = re.compile(r'([\w-]+)\s*=\s*"([^"]*)"')

SKIP_TAGS = {"script", "stylesheet", "twine.private", "annotation", "twine.image", "widget-code"}
SKIP_NAMES = {"StoryInit", "StoryData", "StorySettings", "StoryIncludes", "StoryInterface"}

CSS = {
    "sugarcube": "#passages,.passage,#story-caption,#story-title,#story-subtitle,#menu-story,#ui-dialog-body,"
    "#ui-dialog-title{direction:rtl;text-align:right;font-family:'VNArabic',Helvetica,Arial,sans-serif}",
    "harlowe": "tw-story{direction:rtl;font-family:'VNArabic',Helvetica,Arial,sans-serif}tw-passage{text-align:right}",
}


def _attrs(tag: str) -> dict[str, str]:
    return {k.lower(): html.unescape(v) for k, v in _ATTR.findall(tag)}


def line_units(text: str, dialect: str) -> list[tuple[int, int]]:
    """Lines of a passage, where line breaks inside code (multi-line macros) do not count."""
    spans = tm.spans(text, dialect)
    breaks = []
    si = 0
    for m in re.finditer("\n", text):
        p = m.start()
        while si < len(spans) and spans[si][1] <= p:
            si += 1
        if si < len(spans) and spans[si][0] <= p < spans[si][1]:
            continue
        breaks.append(p)
    out, start = [], 0
    for p in breaks + [len(text)]:
        out.append((start, p))
        start = p + 1
    return out


def has_prose(text: str, dialect: str) -> bool:
    pos, prose = 0, []
    for a, b in tm.spans(text, dialect):
        prose.append(text[pos:a])
        pos = b
    prose.append(text[pos:])
    return bool("".join(prose).strip())


def _code_free(label: str, dialect: str) -> bool:
    """A label we can translate (not a variable or expression)."""
    return bool(label.strip()) and has_prose(label, dialect)


class TwineEngine(Engine):
    name = "Twine"

    def __init__(self, root: Path, files: list[Path], dialect: str, story_format: str):
        super().__init__(root)
        self.files = files
        self.markup = dialect
        self.story_format = story_format
        self.warnings: list[str] = []

    @classmethod
    def detect(cls, path: Path) -> "TwineEngine | None":
        p = Path(path)
        candidates = [p] if p.is_file() else sorted(p.glob("*.htm*"))
        root = p.parent if p.is_file() else p
        files = []
        fmt = ""
        for f in candidates:
            if f.suffix.lower() not in (".html", ".htm"):
                continue
            try:
                data = f.read_bytes()
            except OSError:
                continue
            if MARKER in data:
                files.append(f)
                m = _STORY.search(data.decode("utf-8", "replace"))
                if m and not fmt:
                    fmt = _attrs(m.group(0)).get("format", "")
        if not files:
            return None
        dialect = "harlowe" if "harlowe" in fmt.lower() else "sugarcube"
        return cls(root, files, dialect, fmt)

    def describe(self) -> str:
        names = ", ".join(f.name for f in self.files)
        return f"Twine ({self.story_format or 'SugarCube'}) — {names}"

    # -- backup ---------------------------------------------------------------------
    def _backup_path(self, f: Path) -> Path:
        return self.root / BACKUP_DIR / f.relative_to(self.root)

    def _source_text(self, f: Path) -> str:
        b = self._backup_path(f)
        return (b if b.exists() else f).read_text("utf-8")

    # -- passages -------------------------------------------------------------------
    def _passages(self, doc: str):
        for m in _PASSAGE.finditer(doc):
            attrs = _attrs(m.group(1))
            tags = {t.lower() for t in attrs.get("tags", "").split()}
            name = attrs.get("name", "")
            if tags & SKIP_TAGS or name in SKIP_NAMES:
                continue
            yield m, name, html.unescape(m.group(2))

    def extract(self, progress: Progress = _noop) -> list[Entry]:
        self.warnings = []
        d = self.markup
        entries: dict[tuple[str, str], Entry] = {}
        order = [0]

        def add(channel, kind, text, where):
            key = (channel, text)
            if key in entries:
                entries[key].count += 1
                return
            order[0] += 1
            reason = untranslatable_reason(text, d)
            if kind == "dialogue" and reason not in ("empty", "markup only", "numbers/symbols only"):
                reason = None
            entries[key] = Entry(channel, text, kind, "", where, 0, order=order[0], skip_reason=reason)

        for fi, f in enumerate(self.files):
            doc = self._source_text(f)
            for m, name, text in self._passages(doc):
                for a, b in line_units(text, d):
                    unit = text[a:b]
                    if has_prose(unit, d):
                        add("line", "dialogue", unit, name)
                for tok in tm.tokens(text, d):
                    for label in self._labels(tok):
                        add("link", "menu", label, name)
            progress(f"Reading {f.name}", (fi + 1) / len(self.files))
        if not entries:
            raise RuntimeError("No passages with text found in this Twine game.")
        return sorted(entries.values(), key=lambda e: e.order)

    def _labels(self, tok: str) -> list[str]:
        d = self.markup
        out = []
        if tok.startswith("[["):
            sl = tm.split_link(tok, d)
            if sl and _code_free(sl[0], d):
                out.append(sl[0])
            return out
        for _, _, val in tm.macro_labels(tok, d):
            if _code_free(val, d):
                out.append(val)
        for link in re.findall(r"\[\[.*?\]\]", tok):  # <<link [[text|target]]>>
            sl = tm.split_link(link, d)
            if sl and _code_free(sl[0], d):
                out.append(sl[0])
        return out

    # -- patching -------------------------------------------------------------------
    def _translate_passage(self, text: str, lookup) -> str:
        d = self.markup
        # 1) prose lines
        out, pos = [], 0
        for a, b in line_units(text, d):
            unit = text[a:b]
            tr = lookup("line", unit) if has_prose(unit, d) else None
            out.append(text[pos:a])
            out.append(tr if tr else unit)
            pos = b
        out.append(text[pos:])
        text = "".join(out)

        # 2) link labels and link-macro labels (targets untouched)
        def relabel_link(tok: str) -> str:
            sl = tm.split_link(tok, d)
            if not sl:
                return tok
            label, rebuild = sl
            tr = lookup("link", label)
            if not tr:
                return tok
            safe = tr.replace("]]", "] ]").replace("|", "¦").replace("->", "→").replace("<-", "←")
            return rebuild(safe)

        out, pos = [], 0
        for a, b in tm.spans(text, d):
            tok = text[a:b]
            if tok.startswith("[["):
                new = relabel_link(tok)
            else:
                new = tok
                for s, e, val in reversed(tm.macro_labels(tok, d)):
                    tr = lookup("link", val)
                    if tr:
                        new = new[:s] + tm.quote_like(new[s:e], tr) + new[e:]
                new = re.sub(r"\[\[.*?\]\]", lambda m: relabel_link(m.group(0)), new)
            out.append(text[pos:a])
            out.append(new)
            pos = b
        out.append(text[pos:])
        return "".join(out)

    def _style(self) -> str:
        font = base64.b64encode((assets_dir() / "fonts" / "VNArabic-Regular.ttf").read_bytes()).decode()
        return (
            f'<style id="{STYLE_ID}">@font-face{{font-family:"VNArabic";'
            f'src:url(data:font/ttf;base64,{font}) format("truetype")}}{CSS[self.markup]}</style>'
        )

    def apply(self, translations: dict[tuple[str, str], str], progress: Progress = _noop) -> list[str]:
        self.warnings = []
        lookup = lambda ch, text: translations.get((ch, text))  # noqa: E731
        written = []
        for fi, f in enumerate(self.files):
            doc = self._source_text(f)

            def repl(m):
                attrs = _attrs(m.group(1))
                tags = {t.lower() for t in attrs.get("tags", "").split()}
                if tags & SKIP_TAGS or attrs.get("name", "") in SKIP_NAMES:
                    return m.group(0)
                text = html.unescape(m.group(2))
                new = self._translate_passage(text, lookup)
                if new == text:
                    return m.group(0)
                return m.group(1) + html.escape(new, quote=True) + m.group(3)

            new_doc = _PASSAGE.sub(repl, doc)
            head = re.search(r"</head\s*>", new_doc, re.I)
            style = self._style()
            if head:
                new_doc = new_doc[: head.start()] + style + new_doc[head.start() :]
            else:
                new_doc = style + new_doc
            b = self._backup_path(f)
            if not b.exists():
                b.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, b)
            f.write_text(new_doc, "utf-8")
            written.append(f.relative_to(self.root).as_posix())
            progress(f"Wrote {f.name}", (fi + 1) / len(self.files))
        (self.root / BACKUP_DIR / "manifest.json").write_text(json.dumps({"modified": written}), "utf-8")
        return written

    def remove(self) -> list[str]:
        backup = self.root / BACKUP_DIR
        restored = []
        if backup.is_dir():
            for b in backup.rglob("*"):
                if b.is_file() and b.name != "manifest.json":
                    target = self.root / b.relative_to(backup)
                    shutil.copy2(b, target)
                    restored.append(target.relative_to(self.root).as_posix())
            shutil.rmtree(backup)
        return restored

    def is_applied(self) -> bool:
        return (self.root / BACKUP_DIR).is_dir()
