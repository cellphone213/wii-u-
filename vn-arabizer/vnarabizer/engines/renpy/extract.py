"""Find the text a player actually reads in a Ren'Py game.

Only these are collected:

* dialogue (``say`` statements) and menu choices,
* character display names,
* screen text (``text``, ``textbutton``, ``label``) and strings the game
  marked with ``_()``.

Image names, labels, variables, file paths and other code are never
collected, so they can never be translated by accident.
"""

from __future__ import annotations

import ast as pyast
import re
from dataclasses import dataclass, field

from ..base import Entry
from ...core.protect import untranslatable_reason
from .rpyc import kind, location, pycode_source, walk

CHARACTER_FUNCS = {"Character", "ADVCharacter", "NVLCharacter", "SpeakerCharacter"}
TEXT_DISPLAYABLES = {"Text", "_textbutton", "_label", "sl2text", "text", "textbutton", "label"}
_UNDERSCORE_CALL = re.compile(r"""\b(?:_|__|_p)\(\s*(?:u|r)?("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')\s*\)""")


@dataclass
class Collector:
    entries: dict[tuple[str, str], Entry] = field(default_factory=dict)
    characters: dict[str, str] = field(default_factory=dict)  # variable -> display name
    order: int = 0

    def add(self, channel: str, text: str, kind_: str, file: str = "", line: int = 0, speaker: str = "") -> None:
        if not isinstance(text, str) or not text.strip():
            return
        text = str(text)
        key = (channel, text)
        e = self.entries.get(key)
        if e is not None:
            e.count += 1
            return
        self.order += 1
        self.entries[key] = Entry(
            channel=channel,
            original=text,
            kind=kind_,
            speaker=speaker,
            file=file,
            line=line,
            order=self.order,
            skip_reason=untranslatable_reason(text) if kind_ != "dialogue" else _dialogue_skip(text),
        )


def _dialogue_skip(text: str) -> str | None:
    reason = untranslatable_reason(text)
    # Dialogue is prose by definition; only skip when nothing readable is left.
    return reason if reason in ("empty", "markup only", "numbers/symbols only") else None


def _literal(expr: str):
    try:
        v = pyast.literal_eval(expr.strip())
    except Exception:
        return None
    return v if isinstance(v, str) else None


def _string_arg(node: pyast.AST) -> str | None:
    """A plain string literal, or _("...") around one."""
    if isinstance(node, pyast.Constant) and isinstance(node.value, str):
        return node.value
    if (
        isinstance(node, pyast.Call)
        and isinstance(node.func, pyast.Name)
        and node.func.id in ("_", "__", "_p")
        and node.args
    ):
        return _string_arg(node.args[0])
    return None


def _func_name(f: pyast.AST) -> str:
    if isinstance(f, pyast.Name):
        return f.id
    if isinstance(f, pyast.Attribute):
        return f.attr
    return ""


def scan_python(source: str, col: Collector, file: str, line: int, varname: str | None = None) -> None:
    """Pick up Character names and _()-marked strings from Python code."""
    try:
        tree = pyast.parse(source.strip())
    except SyntaxError:
        tree = None

    if tree is not None:
        for node in pyast.walk(tree):
            if isinstance(node, pyast.Call):
                name = _func_name(node.func)
                if name in CHARACTER_FUNCS and node.args:
                    disp = _string_arg(node.args[0])
                    if disp:
                        col.add("strings", disp, "name", file, line)
                        if varname:
                            col.characters.setdefault(varname, disp)
                elif name in ("_", "__", "_p") and node.args:
                    s = _string_arg(node.args[0])
                    if s:
                        col.add("strings", s, "ui", file, line)
            elif isinstance(node, pyast.Assign) and isinstance(node.value, pyast.Call):
                if _func_name(node.value.func) in CHARACTER_FUNCS and node.value.args:
                    disp = _string_arg(node.value.args[0])
                    for t in node.targets:
                        if isinstance(t, pyast.Name) and disp:
                            col.characters.setdefault(t.id, disp)
    else:
        # Python 2 only syntax: fall back to finding _("...") calls.
        for m in _UNDERSCORE_CALL.finditer(source):
            s = _literal(m.group(1))
            if s:
                col.add("strings", s, "ui", file, line)


def collect(stmts, file_hint: str, col: Collector, ui_only: bool = False) -> None:
    """Collect translatable text from one script's statement list.

    ``ui_only`` is used for Ren'Py's own common scripts, where only
    interface strings matter.
    """
    for node in walk(stmts):
        k = kind(node)
        fn, ln = location(node)
        fn = fn or file_hint

        if k == "Translate" and getattr(node, "language", None) not in (None, "None"):
            # Translations into another language: not source text.
            node.__dict__.pop("block", None)
            continue
        if k in ("TranslateString", "TranslateBlock", "TranslateEarlyBlock", "TranslatePython"):
            node.__dict__.clear()
            continue

        if not ui_only and k in ("Say", "TranslateSay"):
            what = getattr(node, "what", None)
            if isinstance(what, str):
                who = getattr(node, "who", None)
                speaker = ""
                if isinstance(who, str):
                    lit = _literal(who)
                    if lit is not None:  # "Mysterious Man" "..." style
                        speaker = lit
                        col.add("strings", lit, "name", fn, ln)
                    else:
                        speaker = who.strip()
                col.add("say", what, "dialogue", fn, ln, speaker)

        elif not ui_only and k == "Menu":
            for item in getattr(node, "items", None) or []:
                if isinstance(item, (tuple, list)) and item and isinstance(item[0], str):
                    label = item[0]
                    block = item[2] if len(item) > 2 else None
                    col.add("say", label, "menu" if block is not None else "dialogue", fn, ln)

        elif k in ("Define", "Default"):
            code = getattr(node, "code", None)
            src = pycode_source(code) if code is not None else None
            if src:
                scan_python(src, col, fn, ln, getattr(node, "varname", None))

        elif k == "PyCode":
            src = pycode_source(node)
            if src and ("_(" in src or "Character" in src):
                scan_python(src, col, fn, ln)

        elif k == "SLDisplayable":
            disp = getattr(node, "displayable", None)
            dname = getattr(type(disp), "_name", None) if disp is not None else None
            if dname is None and disp is not None:
                dname = getattr(disp, "_name", None)
            positional = getattr(node, "positional", None) or []
            if dname in TEXT_DISPLAYABLES and positional:
                expr = positional[0]
                if isinstance(expr, str):
                    s = _literal(expr)
                    if s is None:
                        try:
                            s = _string_arg(pyast.parse(expr.strip(), mode="eval").body)
                        except SyntaxError:
                            s = None
                    if s:
                        col.add("strings", s, "ui", fn, ln)
            for kw in getattr(node, "keyword", None) or []:
                if isinstance(kw, (tuple, list)) and len(kw) == 2 and kw[0] in ("tooltip", "alt") and isinstance(kw[1], str):
                    s = _literal(kw[1])
                    if s:
                        col.add("strings", s, "ui", fn, ln)

        elif isinstance(node, str):
            pass


def finish(col: Collector) -> list[Entry]:
    """Resolve speaker variables to display names and return entries in reading order."""
    out = list(col.entries.values())
    for e in out:
        if e.channel == "say" and e.speaker in col.characters:
            e.speaker = col.characters[e.speaker]
    out.sort(key=lambda e: e.order)
    return out
