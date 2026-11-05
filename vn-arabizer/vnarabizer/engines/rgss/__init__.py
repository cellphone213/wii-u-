"""RPG Maker XP / VX / VX Ace (Game.rgssad, Game.rgss2a, Game.rgss3a or a loose Data folder).

Data files are Ruby Marshal dumps (.rxdata / .rvdata / .rvdata2), read and
written by ``archives.rmarshal`` so everything except the translated strings
stays byte-identical. Encrypted archives are rebuilt by ``archives.rgss``.

Text the player sees:
  * Show Text (101/401) and Scroll Text (105/405), choices (102),
    Change Name / Nickname (320/324),
  * database names, descriptions and battle messages,
  * system terms and the ``Vocab`` script's message constants,
  * map display names (VX Ace).

RGSS draws text left to right with no Arabic shaping, so translations are
written shaped and in visual order (as for the Japanese engines). A small
script is added before ``Main`` that switches to the bundled Arabic font,
right-aligns message lines, and (VX Ace) puts the battler's name on the
right side of "name + message" battle lines.
"""

from __future__ import annotations

import random
import re
import shutil
import zlib
from pathlib import Path

from ...archives.rgss import RgssArchive
from ...archives.rmarshal import RArray, RHash, RObject, RString, dumps, loads, rstring
from ...core import arabic_visual
from ...core.paths import assets_dir
from ...core.protect import SYNTAXES, untranslatable_reason
from ..base import Entry, Progress, _noop
from ..vntp.base import VNTPEngine

TOKENS = re.compile(rf"(?:{SYNTAXES['rpgm'].pattern})", SYNTAXES["rpgm"].flags)
ARCHIVES = ("Game.rgss3a", "Game.rgss2a", "Game.rgssad")
FONT_NAME = "VN Arabic"
SCRIPT_NAME = "VN Arabizer"

# Per version: data extension, message width in ems (no face, face), help width (None = one line).
VERSIONS = {
    "vxace": (".rvdata2", 20.5, 16.0, 20.5),
    "vx": (".rvdata", 24.0, 18.5, None),
    "xp": (".rxdata", 19.0, 19.0, None),
}
DB_FIELDS = {
    "Actors": ("@name", "@nickname", "@description"),
    "Classes": ("@name",),
    "Skills": ("@name", "@description", "@message1", "@message2"),
    "Items": ("@name", "@description"),
    "Weapons": ("@name", "@description"),
    "Armors": ("@name", "@description"),
    "Enemies": ("@name",),
    "States": ("@name", "@message1", "@message2", "@message3", "@message4"),
}
MULTILINE = {"@description"}
SYSTEM_LISTS = ("@elements", "@skill_types", "@weapon_types", "@armor_types")
_VOCAB_LINE = re.compile(r'^(\s*[A-Z]\w*\s*=\s*)"((?:[^"\\\n]|\\.)*)"(\s*(?:#.*)?)$', re.M)
_PRINTF = re.compile(r"%(\d+\$)?[sd]")
_LETTER = re.compile(r"[^\W\d_]")
_COLOR = re.compile(r"\\[Cc]\[\d+\]")
# Codes that stay at the start/end of a message (waits, instant display).
EDGE = re.compile(r"\\[.|!><^]")


def fix_colors(lines: list[str], state: str = "\\C[0]") -> list[str]:
    """Recolour visual-order lines: RGSS applies \\C[n] to what is drawn after it (to its right),
    but after reordering each colour code sits at the right end of its own span."""
    if not any(_COLOR.search(l) for l in lines):
        return lines
    out = []
    for line in lines:
        parts = _COLOR.split(line)  # s0, s1, ... sk (visual, left to right)
        codes = _COLOR.findall(line)  # c1 ... ck
        # Logically the line reads sk, ck, ..., s1, c1, s0: sk keeps the incoming colour, s(j-1) takes cj.
        colours = codes + [state]
        new = "".join(colours[j] + parts[j] for j in range(len(parts)) if parts[j])
        out.append(new or line)
        if codes:
            state = codes[0]
    return out


def visual_message(text: str, width: float) -> list[str]:
    return fix_colors(arabic_visual.visual_text(text, width, TOKENS, newline="\n", edge_rx=EDGE).split("\n"))

RUBY = r'''# VN Arabizer: Arabic font and right-to-left text for translated games.
begin
  vnar_font = File.expand_path("Fonts/VN Arabic.ttf")
  if File.exist?(vnar_font)
    Win32API.new("gdi32", "AddFontResourceExA", ["p", "l", "l"], "l").call(vnar_font, 0x10, 0) rescue nil
  end
rescue Exception
end
vnar_names = Font.default_name.is_a?(Array) ? Font.default_name : [Font.default_name]
Font.default_name = ["VN Arabic"] + vnar_names

if defined?(Window_Message) && Window_Message.method_defined?(:process_new_line) &&
   Window_Message.method_defined?(:new_page)
  class Window_Message
    alias vnar_new_page new_page
    def new_page(text, pos)
      vnar_new_page(text, pos)
      vnar_align(text, pos)
    end
    alias vnar_process_new_line process_new_line
    def process_new_line(text, pos)
      vnar_process_new_line(text, pos)
      vnar_align(text, pos)
    end
    def vnar_align(text, pos)
      line = text[/\A[^\n]*/] || ""
      icons = line.scan(/\eI\[\d+\]/i).size
      plain = line.gsub(/\e[A-Z]+\[\d+\]/i, "").gsub(/\e./, "")
      width = text_size(plain).width + icons * 24
      x = contents_width - width - 4
      pos[:x] = x if x > pos[:x]
    rescue Exception
    end
  end
end

if defined?(Window_BattleLog)
  class Window_BattleLog
    def vnar_rotate(text)
      n = @vnar_name
      if n.is_a?(String) && !n.empty? && text.is_a?(String) && text[0, n.size] == n
        text = text[n.size..-1] + n
      end
      text
    end
    [:add_text, :replace_text].each do |m|
      next unless method_defined?(m)
      alias_method("vnar_#{m}", m)
      define_method(m) { |text| send("vnar_#{m}", vnar_rotate(text)) }
    end
    [:display_use_item, :display_added_states, :display_removed_states, :display_current_state].each do |m|
      next unless method_defined?(m)
      alias_method("vnar_#{m}", m)
      define_method(m) do |*args|
        skill_or_state = m != :display_use_item || (defined?(RPG::Skill) && args[1].is_a?(RPG::Skill))
        @vnar_name = skill_or_state ? (args[0].name rescue nil) : nil
        begin
          send("vnar_#{m}", *args)
        ensure
          @vnar_name = nil
        end
      end
    end
  end
end
'''


def _is_str(n) -> bool:
    return isinstance(n, RString)


def _number_printf(text: str) -> str:
    """'%sは %s' -> '%1$sは %2$s' so the order survives reordering into Arabic."""
    if "%" not in text or re.search(r"%\d+\$", text):
        return text
    n = 0

    def rep(m):
        nonlocal n
        n += 1
        return f"%{n}${m.group(0)[-1]}"

    return _PRINTF.sub(rep, text)


def _ruby_unescape(s: str) -> str:
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), s)


def _ruby_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("#{", "\\#{").replace("\n", "\\n")


class RGSSEngine(VNTPEngine):
    """RPG Maker XP / VX / VX Ace."""

    markup = "rpgm"

    def __init__(self, root: Path, archive: Path | None, version: str):
        super().__init__(root)
        self.archive = archive
        self.rm_version = version
        self.ext, self.msg_em, self.face_em, self.help_em = VERSIONS[version]
        self.line_em = self.msg_em
        self.name = {"vxace": "RPG Maker VX Ace", "vx": "RPG Maker VX", "xp": "RPG Maker XP"}[version]

    # -- detection ---------------------------------------------------------------------
    @classmethod
    def detect(cls, path: Path) -> "RGSSEngine | None":
        p = Path(path)
        if not p.is_dir():
            return None
        for name, version in zip(ARCHIVES, ("vxace", "vx", "xp")):
            f = next((x for x in p.glob("*") if x.name.lower() == name.lower()), None)
            if f is not None:
                return cls(p, f, version)
        for version, (ext, *_rest) in VERSIONS.items():
            data = p / "Data"
            if data.is_dir() and any(x.name.lower() == f"scripts{ext}" for x in data.iterdir()):
                return cls(p, None, version)
        return None

    def describe(self) -> str:
        return f"{self.name} ({self.archive.name if self.archive else 'Data'})"

    def stage_scripts(self, dest: Path) -> None:  # pragma: no cover - not used
        raise NotImplementedError

    def install(self, patched: Path, sjis_ext: Path, progress: Progress) -> list[str]:  # pragma: no cover
        raise NotImplementedError

    # -- data access -------------------------------------------------------------------
    def _arc(self) -> RgssArchive | None:
        return RgssArchive(self.source(self.archive)) if self.archive else None

    def data_names(self, arc: RgssArchive | None) -> list[str]:
        """Data files (archive names like 'Data\\Map001.rvdata2') we read, in a stable order."""
        if arc is not None:
            names = [e.name for e in arc.entries]
        else:
            data = self.root / "Data"
            names = [f"Data\\{f.name}" for f in sorted(data.iterdir()) if f.is_file()]
        wanted = set(DB_FIELDS) | {"CommonEvents", "Troops", "System", "Scripts"}
        out = []
        for n in names:
            base = n.replace("/", "\\").rsplit("\\", 1)[-1]
            stem, _, ext = base.rpartition(".")
            if "." + ext.lower() != self.ext:
                continue
            if stem in wanted or re.fullmatch(r"Map\d+", stem):
                out.append(n)
        return out

    def _read(self, arc: RgssArchive | None, name: str) -> bytes:
        if arc is not None:
            return arc.read(name)
        return self.source(self.root / name.replace("\\", "/")).read_bytes()

    # -- walking -----------------------------------------------------------------------
    def _walk(self, arc, collect=None, lookup=None, progress: Progress = _noop) -> dict[str, bytes]:
        """Visit every visible string; with ``lookup`` return the changed files' new bytes."""
        changed: dict[str, bytes] = {}
        names = self.data_names(arc)
        for i, name in enumerate(names):
            progress(f"Reading {name}", i / max(1, len(names)))
            raw = self._read(arc, name)
            try:
                root = loads(raw)
            except Exception as e:
                self.warnings.append(f"{name}: {e}")
                continue
            stem = name.replace("/", "\\").rsplit("\\", 1)[-1].rpartition(".")[0]
            w = _Walker(self, stem, collect, lookup)
            w.file(root)
            if lookup and w.changed:
                changed[name] = dumps(root)
        return changed

    def extract(self, progress: Progress = _noop) -> list[Entry]:
        self.warnings = []
        entries: dict[tuple[str, str], Entry] = {}

        def collect(channel, kind, text, where, speaker=""):
            key = (channel, text)
            if key in entries:
                entries[key].count += 1
                return
            reason = untranslatable_reason(text, "rpgm")
            if kind == "dialogue" and reason not in ("empty", "markup only", "numbers/symbols only"):
                reason = None
            entries[key] = Entry(channel, text, kind, speaker, where, 0, order=len(entries) + 1, skip_reason=reason)

        self._walk(self._arc(), collect=collect, progress=progress)
        if not entries:
            raise RuntimeError("No text found in the game's data files.")
        progress("Done", 1.0)
        return sorted(entries.values(), key=lambda e: e.order)

    def apply(self, translations: dict[tuple[str, str], str], progress: Progress = _noop) -> list[str]:
        self.warnings = []
        arc = self._arc()

        def lookup(channel, text):
            return translations.get((channel, text))

        changed = self._walk(arc, lookup=lookup, progress=lambda m, f: progress(m, 0.6 * f))
        # Our runtime script goes into Scripts even when nothing else changed.
        scripts = next((n for n in self.data_names(arc) if n.replace("/", "\\").rsplit("\\", 1)[-1].lower().startswith("scripts.")), None)
        if scripts is not None:
            root = loads(changed[scripts]) if scripts in changed else loads(self._read(arc, scripts))
            self._add_runtime_script(root)
            changed[scripts] = dumps(root)
        else:
            self.warnings.append("Scripts data not found: the Arabic font could not be set.")

        written = []
        progress("Writing game data", 0.7)
        if self.archive is not None:
            self.backup(self.archive)
            tmp = self.archive.with_name(self.archive.name + ".vnarabizer-tmp")
            try:
                RgssArchive(self.source(self.archive)).write(tmp, changed, lambda f: progress("Rebuilding archive", 0.7 + 0.25 * f))
                tmp.replace(self.archive)
            finally:
                tmp.unlink(missing_ok=True)
            written.append(self.archive.name)
        else:
            for name, data in changed.items():
                f = self.root / name.replace("\\", "/")
                self.backup(f)
                f.write_bytes(data)
                written.append(name.replace("\\", "/"))

        fonts = self.root / "Fonts"
        fonts.mkdir(exist_ok=True)
        font = fonts / f"{FONT_NAME}.ttf"
        shutil.copy2(assets_dir() / "fonts" / "VNArabic-Regular.ttf", font)
        shutil.copy2(assets_dir() / "fonts" / "OFL.txt", fonts / f"{FONT_NAME} - OFL.txt")
        self._record_added([font, fonts / f"{FONT_NAME} - OFL.txt"])
        written += [f"Fonts/{font.name}"]
        if self.rm_version != "vxace":
            self.warnings.append(
                f"{self.name}: the font is loaded at start-up from the Fonts folder; if Arabic shows as boxes, "
                "install “Fonts/VN Arabic.ttf” in Windows."
            )
        progress("Done", 1.0)
        return written

    def _add_runtime_script(self, scripts) -> None:
        if not isinstance(scripts, RArray):
            return
        items = scripts.items
        items[:] = [s for s in items if not (isinstance(s, RArray) and len(s.items) > 1 and _is_str(s.items[1]) and s.items[1].text == SCRIPT_NAME)]
        template = next((s for s in items if isinstance(s, RArray) and len(s.items) == 3 and _is_str(s.items[2])), None)
        if template is None:
            self.warnings.append("Unexpected Scripts data: the Arabic font could not be set.")
            return
        used = {s.items[0] for s in items if isinstance(s, RArray) and s.items and isinstance(s.items[0], int)}
        sid = random.Random(len(items)).randrange(1, 1 << 29)
        while sid in used:
            sid += 1
        code = RString(zlib.compress(RUBY.encode("utf-8")), list(template.items[2].ivars) if template.items[2].ivars else None)
        entry = RArray([sid, rstring(SCRIPT_NAME, template.items[1] if _is_str(template.items[1]) else None), code])
        main = next((i for i, s in enumerate(items) if isinstance(s, RArray) and len(s.items) > 1 and _is_str(s.items[1]) and s.items[1].text.strip() == "Main"), len(items))
        items.insert(main, entry)

    def remove(self) -> list[str]:
        out = super().remove()
        fonts = self.root / "Fonts"
        if fonts.is_dir() and not any(fonts.iterdir()):
            fonts.rmdir()
        return out


class _Walker:
    def __init__(self, eng: RGSSEngine, stem: str, collect, lookup):
        self.eng = eng
        self.stem = stem
        self.collect = collect
        self.lookup = lookup
        self.changed = 0

    # -- one string --------------------------------------------------------------------
    def text(self, node, channel: str, kind: str, where: str, width: float | None = None, original: str | None = None):
        """Collect, or replace in place (shaped, visual order). ``width`` wraps with '\\n'."""
        if not _is_str(node):
            return
        src = node.text if original is None else original
        if not src.strip() or not _LETTER.search(src):
            return
        if self.collect:
            self.collect(channel, kind, src, where)
            return
        tr = self.lookup(channel, src) if self.lookup else None
        if not tr:
            return
        if width:
            out = "\n".join(visual_message(tr, width))
        else:
            out = fix_colors([arabic_visual.visual_line(tr, TOKENS)])[0]
        node.set(out)
        self.changed += 1

    # -- files -------------------------------------------------------------------------
    def file(self, root) -> None:
        s = self.stem
        if s in DB_FIELDS:
            for obj in root.items if isinstance(root, RArray) else []:
                if not isinstance(obj, RObject):
                    continue
                for f in DB_FIELDS[s]:
                    width = self.eng.help_em if f in MULTILINE else None
                    kind = "name" if f in ("@name", "@nickname") and s == "Actors" else "ui"
                    self.text(obj.get(f), "db", kind, f"{s}:{obj.get('@id', 0)}{f}", width)
        elif s == "CommonEvents":
            for ev in root.items if isinstance(root, RArray) else []:
                if isinstance(ev, RObject):
                    self.events(ev.get("@list"), f"{s}:{ev.get('@id', 0)}")
        elif s == "Troops":
            for tr in root.items if isinstance(root, RArray) else []:
                if isinstance(tr, RObject):
                    for pi, page in enumerate(getattr(tr.get("@pages"), "items", []) or []):
                        if isinstance(page, RObject):
                            self.events(page.get("@list"), f"{s}:{tr.get('@id', 0)}/{pi + 1}")
        elif s == "System":
            self.system(root)
        elif s == "Scripts":
            self.scripts(root)
        elif s.startswith("Map") and isinstance(root, RObject):
            self.text(root.get("@display_name"), "db", "ui", f"{s}:display_name")
            events = root.get("@events")
            for _, ev in events.pairs if isinstance(events, RHash) else []:
                if not isinstance(ev, RObject):
                    continue
                for pi, page in enumerate(getattr(ev.get("@pages"), "items", []) or []):
                    if isinstance(page, RObject):
                        self.events(page.get("@list"), f"{s}:EV{ev.get('@id', 0):03d}/{pi + 1}")

    def system(self, root) -> None:
        if not isinstance(root, RObject):
            return
        self.text(root.get("@game_title"), "db", "ui", "System:game_title")
        self.text(root.get("@currency_unit"), "db", "ui", "System:currency_unit")
        for f in SYSTEM_LISTS:
            arr = root.get(f)
            for i, n in enumerate(arr.items if isinstance(arr, RArray) else []):
                self.text(n, "db", "ui", f"System:{f}[{i}]")
        for holder in ("@terms", "@words"):
            terms = root.get(holder)
            if not isinstance(terms, RObject):
                continue
            for k, v in terms.ivars:
                if isinstance(v, RArray):
                    for i, n in enumerate(v.items):
                        self.text(n, "db", "ui", f"System:{k}[{i}]")
                else:
                    self.text(v, "db", "ui", f"System:{k}")

    def scripts(self, root) -> None:
        for s in root.items if isinstance(root, RArray) else []:
            if not (isinstance(s, RArray) and len(s.items) == 3 and _is_str(s.items[1]) and _is_str(s.items[2])):
                continue
            try:
                code = zlib.decompress(s.items[2].data).decode("utf-8")
            except Exception:
                continue
            if s.items[1].text.strip() != "Vocab" and "module Vocab" not in code:
                continue
            new_code = self._vocab(code)
            if new_code != code:
                s.items[2].data = zlib.compress(new_code.encode("utf-8"))
                self.changed += 1

    def _vocab(self, code: str) -> str:
        def rep(m):
            value = _ruby_unescape(m.group(2))
            if not _LETTER.search(value):
                return m.group(0)
            numbered = _number_printf(value)
            name = m.group(1).strip().split("=")[0].strip()
            if self.collect:
                self.collect("vocab", "ui", numbered, f"Vocab::{name}")
                return m.group(0)
            tr = self.lookup("vocab", numbered) if self.lookup else None
            if not tr:
                return m.group(0)
            out = fix_colors([arabic_visual.visual_line(tr, TOKENS)])[0]
            return f'{m.group(1)}"{_ruby_escape(out)}"{m.group(3)}'

        return _VOCAB_LINE.sub(rep, code)

    # -- event commands ----------------------------------------------------------------
    def events(self, lst, where: str) -> None:
        if not isinstance(lst, RArray):
            return
        items = lst.items
        xp = self.eng.rm_version == "xp"
        out = []
        i = 0
        face = False
        while i < len(items):
            cmd = items[i]
            code = cmd.get("@code") if isinstance(cmd, RObject) else None
            params = cmd.get("@parameters") if isinstance(cmd, RObject) else None
            p = params.items if isinstance(params, RArray) else []
            if code in (101, 105) and not (xp and code == 105):
                line_code = 401 if code == 101 else 405
                if code == 101 and not xp:
                    face = bool(p and _is_str(p[0]) and p[0].text)
                j = i + 1
                lines_cmds = [cmd] if xp else []
                while j < len(items) and isinstance(items[j], RObject) and items[j].get("@code") == line_code:
                    lines_cmds.append(items[j])
                    j += 1
                strings = []
                for c in lines_cmds:
                    pp = c.get("@parameters")
                    strings.append(pp.items[0] if isinstance(pp, RArray) and pp.items and _is_str(pp.items[0]) else None)
                if not xp:
                    out.append(cmd)
                if strings and all(s is not None for s in strings):
                    block = "\n".join(s.text for s in strings)
                    # line_em may be changed in the settings; the face takes the same room either way.
                    full = self.eng.line_em
                    width = full - (self.eng.msg_em - self.eng.face_em) if (face and line_code == 401) else full
                    new_lines = self._message(block, where, width)
                    if new_lines is not None:
                        out.extend(self._line_cmds(lines_cmds, strings, new_lines, line_code, xp))
                        i = j
                        continue
                out.extend(lines_cmds)
                i = j
                continue
            if code == 102 and p and isinstance(p[0], RArray):
                for n in p[0].items:
                    self.text(n, "choice", "menu", where)
            elif code in (320, 324) and len(p) > 1:
                self.text(p[1], "name", "name", where)
            out.append(cmd)
            i += 1
        if out != items:
            lst.items = out

    def _message(self, block: str, where: str, width: float) -> list[str] | None:
        if not block.strip() or not _LETTER.search(block):
            return None
        if self.collect:
            self.collect("message", "dialogue", block, where)
            return None
        tr = self.lookup("message", block) if self.lookup else None
        if not tr:
            return None
        self.changed += 1
        return visual_message(tr, width)

    @staticmethod
    def _line_cmds(cmds: list[RObject], strings: list[RString], lines: list[str], line_code: int, xp: bool) -> list[RObject]:
        """New text commands for ``lines``, reusing the originals and cloning the last as needed."""
        out = []
        for k, text in enumerate(lines):
            if k < len(cmds):
                strings[k].set(text)
                out.append(cmds[k])
                continue
            tmpl = cmds[-1]
            if xp and tmpl.get("@code") == 101:
                code_val = 401
            else:
                code_val = line_code
            new = RObject(tmpl.cls, [])
            for name, value in tmpl.ivars:
                if name == "@code":
                    value = code_val
                elif name == "@parameters":
                    value = RArray([rstring(text, strings[-1])], value.ivars if isinstance(value, RArray) else None)
                new.ivars.append((name, value))
            out.append(new)
        return out  # fewer lines than before: the extra commands are dropped


__all__ = ["RGSSEngine"]
