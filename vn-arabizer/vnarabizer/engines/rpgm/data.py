"""Find and replace player-visible text in RPG Maker MV/MZ data files.

Only text the player reads is touched:

* messages (Show Text 101/401), scrolling text (105/405),
* choices (102/402), speaker names (MZ 101), name/nickname/profile changes,
* database names, descriptions and battle messages, system terms,
  map display names.

Never touched: notes (plugin data), scripts (355/655), plugin commands,
comments, file names of images/sounds, switch/variable names.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from .wrap import wrap

Collect = Callable[[str, str, str, str, str], None]  # channel, kind, text, where, speaker
Lookup = Callable[[str, str], Optional[str]]  # channel, original -> translation or None

# Database files and the fields shown to the player. Fields marked True are
# multi-line help texts that get re-wrapped.
DB_FIELDS: dict[str, dict[str, bool]] = {
    "Actors.json": {"name": False, "nickname": False, "profile": True},
    "Classes.json": {"name": False},
    "Skills.json": {"name": False, "description": True, "message1": False, "message2": False},
    "Items.json": {"name": False, "description": True},
    "Weapons.json": {"name": False, "description": True},
    "Armors.json": {"name": False, "description": True},
    "Enemies.json": {"name": False},
    "States.json": {"name": False, "message1": False, "message2": False, "message3": False, "message4": False},
}
EVENT_FILES = ("CommonEvents.json", "Troops.json")
SYSTEM_LISTS = ("elements", "skillTypes", "weaponTypes", "armorTypes", "equipTypes")


@dataclass
class Layout:
    """Window geometry used for wrapping (RPG Maker defaults unless the game says otherwise)."""

    mz: bool = False
    box_width: int = 816
    font_size: int = 28

    @property
    def padding(self) -> int:
        return 12 if self.mz else 18

    def message_width(self, face: bool) -> float:
        inner = self.box_width - 2 * self.padding
        margin = (144 + 20 if face else 4) if self.mz else (168 if face else 0)
        return (inner - margin) * 0.95

    def full_width(self) -> float:
        return (self.box_width - 2 * self.padding - 12) * 0.95


class Walker:
    def __init__(self, layout: Layout, collect: Collect | None = None, lookup: Lookup | None = None):
        self.layout = layout
        self.collect = collect
        self.lookup = lookup
        self.changed = 0

    # -- helpers ------------------------------------------------------------
    def _text(self, channel: str, kind: str, text, where: str, speaker: str = "", wrap_width: float | None = None):
        """Collect ``text`` or return its replacement (or the text unchanged)."""
        if not isinstance(text, str) or not text.strip():
            return text
        if self.collect:
            self.collect(channel, kind, text, where, speaker)
            return text
        if self.lookup:
            tr = self.lookup(channel, text)
            if tr:
                self.changed += 1
                if wrap_width:
                    return "\n".join(wrap(tr, wrap_width, self.layout.font_size))
                return tr
        return text

    # -- event command lists ------------------------------------------------
    def event_list(self, lst, where: str):
        if not isinstance(lst, list):
            return lst
        out = []
        i = 0
        speaker = ""
        face = False
        while i < len(lst):
            cmd = lst[i]
            if not isinstance(cmd, dict):
                out.append(cmd)
                i += 1
                continue
            code = cmd.get("code")
            params = cmd.get("parameters") or []

            if code in (101, 105):
                # Message header, then its text lines (401) or scrolling lines (405).
                line_code = 401 if code == 101 else 405
                if code == 101:
                    face = bool(params and params[0])
                    speaker = params[4] if len(params) > 4 and isinstance(params[4], str) else ""
                    if speaker:
                        new_speaker = self._text("name", "name", speaker, where)
                        if new_speaker != speaker:
                            cmd = dict(cmd, parameters=params[:4] + [new_speaker] + params[5:])
                out.append(cmd)
                j = i + 1
                lines = []
                while j < len(lst) and isinstance(lst[j], dict) and lst[j].get("code") == line_code:
                    p = lst[j].get("parameters") or [""]
                    lines.append(p[0] if p and isinstance(p[0], str) else "")
                    j += 1
                block = "\n".join(lines)
                width = self.layout.message_width(face) if code == 101 else self.layout.full_width()
                hint = speaker or (params[0] if code == 101 and params and isinstance(params[0], str) else "")
                new_block = self._text("message", "dialogue", block, where, hint)
                if new_block is not block and new_block != block and self.lookup:
                    indent = lst[i + 1].get("indent", 0) if j > i + 1 else cmd.get("indent", 0)
                    new_lines = wrap(new_block, width, self.layout.font_size)
                    for ln in new_lines:
                        out.append({"code": line_code, "indent": indent, "parameters": [ln]})
                else:
                    out.extend(lst[i + 1 : j])
                i = j
                continue

            if code == 102 and params and isinstance(params[0], list):
                choices = [self._text("choice", "menu", c, where) for c in params[0]]
                if choices != params[0]:
                    cmd = dict(cmd, parameters=[choices] + list(params[1:]))
            elif code == 402 and len(params) > 1:
                new = self._text("choice", "menu", params[1], where)
                if new != params[1]:
                    cmd = dict(cmd, parameters=[params[0], new] + list(params[2:]))
            elif code in (320, 324) and len(params) > 1:
                new = self._text("name", "name", params[1], where)
                if new != params[1]:
                    cmd = dict(cmd, parameters=[params[0], new] + list(params[2:]))
            elif code == 325 and len(params) > 1:
                new = self._text("db", "ui", params[1], where, wrap_width=self.layout.full_width())
                if new != params[1]:
                    cmd = dict(cmd, parameters=[params[0], new] + list(params[2:]))
            out.append(cmd)
            i += 1
        return out

    # -- files ----------------------------------------------------------------
    def map_file(self, data: dict, fname: str) -> dict:
        if not isinstance(data, dict):
            return data
        dn = data.get("displayName")
        new = self._text("db", "ui", dn, f"{fname}:displayName")
        if new != dn:
            data["displayName"] = new
        for ev in data.get("events") or []:
            if not isinstance(ev, dict):
                continue
            for pi, page in enumerate(ev.get("pages") or []):
                if isinstance(page, dict) and "list" in page:
                    page["list"] = self.event_list(page["list"], f"{fname}:EV{ev.get('id', 0):03d}/{pi + 1}")
        return data

    def common_events(self, data, fname: str):
        for ev in data or []:
            if isinstance(ev, dict) and "list" in ev:
                ev["list"] = self.event_list(ev["list"], f"{fname}:{ev.get('id', 0)} {ev.get('name', '')}")
        return data

    def troops(self, data, fname: str):
        for tr in data or []:
            if not isinstance(tr, dict):
                continue
            for pi, page in enumerate(tr.get("pages") or []):
                if isinstance(page, dict) and "list" in page:
                    page["list"] = self.event_list(page["list"], f"{fname}:{tr.get('id', 0)}/{pi + 1}")
        return data

    def database(self, data, fname: str):
        fields = DB_FIELDS[fname]
        for obj in data or []:
            if not isinstance(obj, dict):
                continue
            for field, multiline in fields.items():
                if field in obj:
                    kind = "name" if field == "name" and fname == "Actors.json" else "ui"
                    obj[field] = self._text(
                        "db", kind, obj[field], f"{fname}:{obj.get('id', 0)}.{field}",
                        wrap_width=self.layout.full_width() if multiline else None,
                    )
        return data

    def system(self, data: dict, fname: str = "System.json"):
        if not isinstance(data, dict):
            return data
        for key in ("gameTitle", "currencyUnit"):
            if key in data:
                data[key] = self._text("system", "ui", data[key], f"{fname}:{key}")
        for key in SYSTEM_LISTS:
            if isinstance(data.get(key), list):
                data[key] = [self._text("system", "ui", v, f"{fname}:{key}") for v in data[key]]
        terms = data.get("terms")
        if isinstance(terms, dict):
            for key in ("basic", "commands", "params"):
                if isinstance(terms.get(key), list):
                    terms[key] = [self._text("system", "ui", v, f"{fname}:terms.{key}") for v in terms[key]]
            msgs = terms.get("messages")
            if isinstance(msgs, dict):
                for k, v in msgs.items():
                    msgs[k] = self._text("system", "ui", v, f"{fname}:terms.messages.{k}")
        return data

    def file(self, fname: str, data):
        """Dispatch on file name. Returns the (possibly modified) data."""
        if fname.startswith("Map") and fname[3:6].isdigit():
            return self.map_file(data, fname)
        if fname == "CommonEvents.json":
            return self.common_events(data, fname)
        if fname == "Troops.json":
            return self.troops(data, fname)
        if fname in DB_FIELDS:
            return self.database(data, fname)
        if fname == "System.json":
            return self.system(data, fname)
        return data


def is_text_file(fname: str) -> bool:
    return (
        (fname.startswith("Map") and fname[3:6].isdigit() and fname.endswith(".json"))
        or fname in EVENT_FILES
        or fname in DB_FIELDS
        or fname == "System.json"
    )
