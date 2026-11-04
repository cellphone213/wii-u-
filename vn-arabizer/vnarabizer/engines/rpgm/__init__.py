"""RPG Maker MV and MZ support.

Data files are plain JSON. Applying a translation:

1. backs up every file it will change into ``vnarabizer_backup/`` (once),
2. rewrites the data files from the pristine backup with Arabic text,
   re-wrapped to the message window width,
3. adds the ``VNArabizer_RTL`` plugin and the Arabic font,
4. registers the plugin by appending one line to ``js/plugins.js``.

"Remove" copies the backups back byte-for-byte and deletes the added files.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from ...core.paths import assets_dir
from ...core.protect import untranslatable_reason
from ..base import Engine, Entry, Progress, _noop
from .data import Layout, Walker, is_text_file

BACKUP_DIR = "vnarabizer_backup"
PLUGIN_NAME = "VNArabizer_RTL"
FONT_NAME = "VNArabic-Regular.ttf"
MANIFEST = f"{BACKUP_DIR}/manifest.json"
PLUGIN_LINE = '$plugins.push({"name":"%s","status":true,"description":"Arabic support (VN Arabizer)","parameters":{}}); // VN Arabizer' % PLUGIN_NAME


def _load_json(path: Path):
    return json.loads(path.read_text("utf-8-sig"))


def _dump_json(data) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


class RPGMakerEngine(Engine):
    name = "RPG Maker"
    markup = "rpgm"

    def __init__(self, root: Path, web: Path, mz: bool):
        super().__init__(root)
        self.web = web  # folder with index.html, js/, data/
        self.mz = mz
        self.warnings: list[str] = []

    # -- detection ---------------------------------------------------------------
    @classmethod
    def detect(cls, path: Path) -> "RPGMakerEngine | None":
        p = Path(path)
        if p.name.lower() in ("www", "data", "js") and not (p / "js").is_dir():
            p = p.parent
        if p.name.lower() == "www":
            p = p.parent
        for web in (p / "www", p):
            if not (web / "data" / "System.json").is_file():
                continue
            if (web / "js" / "rmmz_core.js").is_file():
                return cls(p, web, mz=True)
            if (web / "js" / "rpg_core.js").is_file():
                return cls(p, web, mz=False)
        return None

    @property
    def data_dir(self) -> Path:
        return self.web / "data"

    @property
    def version(self) -> str:
        core = self.web / "js" / ("rmmz_core.js" if self.mz else "rpg_core.js")
        try:
            head = core.read_text("utf-8", "replace")[:200000]
        except OSError:
            return ""
        m = re.search(r"RPGMAKER_VERSION\s*=\s*['\"]([\d.]+)", head)
        return m.group(1) if m else ""

    def describe(self) -> str:
        v = f" {self.version}" if self.version else ""
        return f"RPG Maker {'MZ' if self.mz else 'MV'}{v}"

    def layout(self) -> Layout:
        lay = Layout(mz=self.mz, font_size=26 if self.mz else 28)
        try:
            system = _load_json(self._source(self.data_dir / "System.json"))
            adv = system.get("advanced") or {}
            if self.mz:
                lay.box_width = int(adv.get("uiAreaWidth") or adv.get("screenWidth") or 816)
                lay.font_size = int(adv.get("fontSize") or 26)
        except Exception:
            pass
        if not self.mz:
            # Screen size and font size are often changed by plugin parameters.
            for plugin in self._plugins():
                params = {k.lower().replace(" ", ""): v for k, v in (plugin.get("parameters") or {}).items()}
                if not plugin.get("status"):
                    continue
                for key in ("screenwidth",):
                    if str(params.get(key, "")).isdigit():
                        lay.box_width = int(params[key])
                for key in ("fontsize", "defaultfontsize"):
                    if str(params.get(key, "")).isdigit():
                        lay.font_size = int(params[key])
        return lay

    def _plugins(self) -> list[dict]:
        f = self._source(self.web / "js" / "plugins.js")
        try:
            text = f.read_text("utf-8-sig")
            start = text.index("[", text.index("$plugins"))
            end = text.rindex("]")
            return json.loads(text[start : end + 1])
        except Exception:
            return []

    # -- backup handling -----------------------------------------------------------
    def _backup_path(self, original: Path) -> Path:
        return self.root / BACKUP_DIR / original.relative_to(self.root)

    def _source(self, original: Path) -> Path:
        """The pristine copy of a game file: the backup if one exists."""
        b = self._backup_path(original)
        return b if b.exists() else original

    def _backup(self, original: Path) -> None:
        b = self._backup_path(original)
        if not b.exists():
            b.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, b)

    def _text_files(self) -> list[Path]:
        return sorted(p for p in self.data_dir.glob("*.json") if is_text_file(p.name))

    # -- extraction ----------------------------------------------------------------
    def extract(self, progress: Progress = _noop) -> list[Entry]:
        self.warnings = []
        entries: dict[tuple[str, str], Entry] = {}
        order = [0]

        def collect(channel, kind, text, where, speaker):
            key = (channel, text)
            e = entries.get(key)
            if e:
                e.count += 1
                return
            order[0] += 1
            reason = untranslatable_reason(text, "rpgm")
            if kind == "dialogue" and reason not in ("empty", "markup only", "numbers/symbols only"):
                reason = None
            fname, _, loc = where.partition(":")
            entries[key] = Entry(channel, text, kind, speaker, fname, 0, order=order[0], skip_reason=reason, extra={"where": loc})

        files = self._text_files()
        if not files:
            raise RuntimeError("No RPG Maker data files found.")
        walker = Walker(self.layout(), collect=collect)
        # Database and system first so names appear at the top of the list.
        files.sort(key=lambda p: (not (p.name in ("System.json", "Actors.json")), p.name.startswith("Map"), p.name))
        for i, f in enumerate(files):
            progress(f"Reading {f.name}", i / len(files))
            try:
                walker.file(f.name, _load_json(self._source(f)))
            except Exception as e:
                self.warnings.append(f"{f.name}: {e}")
        progress("Done", 1.0)
        return sorted(entries.values(), key=lambda e: e.order)

    # -- patching ------------------------------------------------------------------
    def apply(self, translations: dict[tuple[str, str], str], progress: Progress = _noop) -> list[str]:
        self.warnings = []
        lookup = lambda ch, text: translations.get((ch, text))  # noqa: E731
        walker = Walker(self.layout(), lookup=lookup)
        written: list[str] = []
        added: list[str] = []

        files = self._text_files()
        for i, f in enumerate(files):
            progress(f"Writing {f.name}", 0.8 * i / max(1, len(files)))
            try:
                data = _load_json(self._source(f))
            except Exception as e:
                self.warnings.append(f"{f.name}: {e}")
                continue
            before = walker.changed
            data = walker.file(f.name, data)
            if walker.changed != before:
                self._backup(f)
                f.write_text(_dump_json(data), "utf-8")
                written.append(f.relative_to(self.root).as_posix())
            elif self._backup_path(f).exists():
                # Nothing to translate any more: put the original back.
                shutil.copy2(self._backup_path(f), f)

        progress("Installing Arabic font and plugin", 0.85)
        fonts = self.web / "fonts"
        fonts.mkdir(exist_ok=True)
        shutil.copy2(assets_dir() / "fonts" / FONT_NAME, fonts / FONT_NAME)
        shutil.copy2(assets_dir() / "fonts" / "OFL.txt", fonts / "VNArabic-OFL.txt")
        plugin_dir = self.web / "js" / "plugins"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(assets_dir() / "rpgm" / f"{PLUGIN_NAME}.js", plugin_dir / f"{PLUGIN_NAME}.js")
        added += [
            (fonts / FONT_NAME).relative_to(self.root).as_posix(),
            (fonts / "VNArabic-OFL.txt").relative_to(self.root).as_posix(),
            (plugin_dir / f"{PLUGIN_NAME}.js").relative_to(self.root).as_posix(),
        ]

        plugins_js = self.web / "js" / "plugins.js"
        if plugins_js.exists():
            self._backup(plugins_js)
            text = self._source(plugins_js).read_text("utf-8-sig")
            plugins_js.write_text(text.rstrip() + "\n" + PLUGIN_LINE + "\n", "utf-8")
            written.append(plugins_js.relative_to(self.root).as_posix())
        else:
            self.warnings.append("js/plugins.js not found: the Arabic plugin could not be registered")

        manifest = self.root / MANIFEST
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({"added": added, "modified": written}, indent=1), "utf-8")
        progress("Done", 1.0)
        return written + added

    def remove(self) -> list[str]:
        removed: list[str] = []
        backup = self.root / BACKUP_DIR
        manifest = self.root / MANIFEST
        if manifest.exists():
            try:
                for rel in json.loads(manifest.read_text("utf-8")).get("added", []):
                    p = self.root / rel
                    if p.is_file():
                        p.unlink()
                        removed.append(rel)
            except Exception:
                pass
        if backup.is_dir():
            for b in backup.rglob("*"):
                if b.is_file() and b != manifest:
                    target = self.root / b.relative_to(backup)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(b, target)
                    removed.append(target.relative_to(self.root).as_posix())
            shutil.rmtree(backup)
        return removed

    def is_applied(self) -> bool:
        return (self.root / BACKUP_DIR).is_dir()
