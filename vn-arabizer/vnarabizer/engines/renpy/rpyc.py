"""Read compiled Ren'Py scripts (.rpyc) and walk their syntax tree.

Works for Ren'Py 6.x, 7.x (Python 2 pickles) and 8.x (Python 3 pickles)
because every node is loaded as an inert placeholder and accessed by name.
"""

from __future__ import annotations

import struct
from typing import Iterator

from .safepickle import FakeBase, decompress, loads

RPYC2_HEADER = b"RENPY RPC2"


class RpycError(Exception):
    pass


def read_rpyc(data: bytes, slot: int = 1):
    """Return the (data, statements) tuple stored in a .rpyc file."""
    if data.startswith(RPYC2_HEADER):
        pos = len(RPYC2_HEADER)
        while True:
            if pos + 12 > len(data):
                raise RpycError("slot not found")
            header_slot, start, length = struct.unpack("<III", data[pos : pos + 12])
            if header_slot == slot:
                break
            if header_slot == 0:
                raise RpycError("slot not found")
            pos += 12
        payload = data[start : start + length]
    else:
        payload = data  # legacy single-slot format
    try:
        obj = loads(decompress(payload))
    except Exception as e:  # obfuscated or corrupt file
        raise RpycError(f"cannot read script: {e}") from e
    if isinstance(obj, tuple) and len(obj) == 2:
        return obj
    raise RpycError("unexpected script layout")


def kind(node) -> str:
    return getattr(type(node), "_name", type(node).__name__)


def module(node) -> str:
    return getattr(type(node), "_module", "")


def walk(root) -> Iterator[FakeBase]:
    """Yield every placeholder object reachable from ``root``, once each."""
    seen: set[int] = set()
    stack = [root]
    while stack:
        obj = stack.pop()
        oid = id(obj)
        if oid in seen:
            continue
        seen.add(oid)
        if isinstance(obj, FakeBase):
            yield obj
            children = list(obj.__dict__.values())
        elif isinstance(obj, (list, tuple, set, frozenset)):
            children = list(obj)
        elif isinstance(obj, dict):
            children = list(obj.values())
        else:
            continue
        # Reverse so that document order is roughly preserved when popping.
        for c in reversed(children):
            if isinstance(c, (FakeBase, list, tuple, dict, set, frozenset)):
                stack.append(c)


def pycode_source(node) -> str | None:
    """Source text of a PyCode placeholder (state tuple: (version, source, location, mode, ...))."""
    st = getattr(node, "_state", None)
    if isinstance(st, tuple) and len(st) >= 2 and isinstance(st[1], str):
        return str(st[1])
    src = getattr(node, "source", None)
    return str(src) if isinstance(src, str) else None


def location(node) -> tuple[str, int]:
    fn = getattr(node, "filename", "") or ""
    ln = getattr(node, "linenumber", 0) or 0
    if not fn:
        name = getattr(node, "name", None)
        if isinstance(name, tuple) and name and isinstance(name[0], str):
            fn = name[0]
    if not isinstance(fn, str):
        fn = str(fn)
    return fn.replace("\\", "/"), int(ln) if isinstance(ln, int) else 0
