"""Build .rpyc/.rpa files shaped like real Ren'Py output, for tests.

The classes are registered under Ren'Py's module names so the pickles
reference ``renpy.ast.Say`` etc., exactly like files written by the engine.
"""

from __future__ import annotations

import copyreg
import pickle
import struct
import sys
import types
import zlib


def _module(name):
    mod = sys.modules.get(name)
    if mod is None:
        mod = types.ModuleType(name)
        sys.modules[name] = mod
    return mod


for _m in ("renpy", "renpy.ast", "renpy.astsupport", "renpy.sl2", "renpy.sl2.slast", "renpy.text", "renpy.text.text", "renpy.ui", "renpy.sl2.sldisplayables"):
    _module(_m)


class CSlotNode:
    """Pickles like Ren'Py 8's cslots objects: __newobj__ + (None, slots)."""

    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __reduce_ex__(self, protocol):
        return (copyreg.__newobj__, (type(self),), (None, dict(self.__dict__)))


class DictNode:
    """Pickles like Ren'Py 6/7 objects: plain __dict__ state."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


class PyExpr(str):
    def __new__(cls, s, filename="game/script.rpy", linenumber=1):
        o = str.__new__(cls, s)
        o.filename = filename
        o.linenumber = linenumber
        return o

    def __reduce__(self):
        return (PyExpr, (str(self), self.filename, self.linenumber, 3, 0, 0))


class PyCode:
    def __init__(self, source):
        self.source = source

    def __getstate__(self):
        return (1, self.source, ("game/script.rpy", 1), "eval", 3, 0, 0)

    def __setstate__(self, state):
        self.source = state[1]


def _register(modname, cls, name=None):
    name = name or cls.__name__
    new = type(name, (cls,), {"__module__": modname, "__qualname__": name})
    setattr(sys.modules[modname], name, new)
    return new


def make_classes(style: str):
    base = CSlotNode if style == "cslots" else DictNode
    ns = {}
    for n in ("Say", "Menu", "Define", "Label", "Init", "Translate", "TranslateString", "Screen", "Python"):
        ns[n] = _register("renpy.ast", base, n)
    ns["SLScreen"] = _register("renpy.sl2.slast", base, "SLScreen")
    ns["SLDisplayable"] = _register("renpy.sl2.slast", base, "SLDisplayable")
    ns["PyExpr"] = _register("renpy.astsupport", PyExpr, "PyExpr")
    ns["PyCode"] = _register("renpy.ast", PyCode, "PyCode")
    ns["Text"] = _register("renpy.text.text", object, "Text")
    ns["_textbutton"] = _register("renpy.ui", object, "_textbutton")
    ns["sl2add"] = _register("renpy.sl2.sldisplayables", object, "sl2add")
    return ns


def sample_script(style: str = "cslots"):
    c = make_classes(style)
    e_def = c["Define"](varname="e", store="store", code=c["PyCode"]('Character("Eileen", color="#c8ffc8")'),
                        filename="game/script.rpy", linenumber=1)
    say1 = c["Say"](who="e", what="Hello, [player]! Welcome to {b}Ren'Py{/b}.", filename="game/script.rpy", linenumber=10)
    say2 = c["Say"](who=None, what="The sun was setting.", filename="game/script.rpy", linenumber=11)
    say3 = c["Say"](who='"Mysterious Man"', what="Who goes there?", filename="game/script.rpy", linenumber=12)
    show = c["Python"](code=c["PyCode"]('renpy.show("eileen happy")'), filename="game/script.rpy", linenumber=13)
    menu = c["Menu"](items=[("What should I do?", "True", None), ("Go left", "True", [say2]), ("Go right", "True", [say1])],
                     filename="game/script.rpy", linenumber=14)
    tl_say = c["Say"](who="e", what="Hallo!", filename="game/tl/german/script.rpy", linenumber=3)
    tl = c["Translate"](identifier="start_abc", language="german", block=[tl_say], filename="game/tl/german/script.rpy", linenumber=2)
    sl_text = c["SLDisplayable"](displayable=c["Text"], positional=[c["PyExpr"]('_("Start Game")')], keyword=[], children=[])
    sl_button = c["SLDisplayable"](displayable=c["_textbutton"], positional=[c["PyExpr"]('"Load"')],
                                   keyword=[("action", c["PyExpr"]('ShowMenu("load")'))], children=[])
    sl_add = c["SLDisplayable"](displayable=c["sl2add"], positional=[c["PyExpr"]('"gui/logo.png"')], keyword=[], children=[])
    screen = c["Screen"](screen=c["SLScreen"](name="main_menu", children=[sl_text, sl_button, sl_add]),
                         filename="game/screens.rpy", linenumber=1)
    label = c["Label"](name="start", block=[say1, say2, say3, show, menu], filename="game/script.rpy", linenumber=9)
    init = c["Init"](block=[e_def], priority=0, filename="game/script.rpy", linenumber=1)
    return ({"version": 5003000, "key": "x"}, [init, label, tl, screen])


def rpyc_bytes(obj, protocol=2, legacy=False) -> bytes:
    payload = zlib.compress(pickle.dumps(obj, protocol), 3)
    if legacy:
        return payload
    header = b"RENPY RPC2"
    table_len = 3 * 12
    start = len(header) + table_len
    table = struct.pack("III", 1, start, len(payload)) + struct.pack("III", 0, 0, 0) * 2
    return header + table + payload


def rpa_bytes(files: dict[str, bytes], key: int = 0x42424242) -> bytes:
    body = bytearray()
    index = {}
    offset = 51  # header length used by Ren'Py
    for name, data in files.items():
        index[name] = [(offset + len(body) ^ key, len(data) ^ key, b"")]
        body += data
    idx = zlib.compress(pickle.dumps(index, 2))
    index_offset = 51 + len(body)
    header = b"RPA-3.0 %016x %08x\n" % (index_offset, key)
    assert len(header) == 34
    header = header.ljust(51, b"Z")
    return header + bytes(body) + idx
