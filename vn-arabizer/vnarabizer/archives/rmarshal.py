"""Ruby Marshal (format 4.8) reader and writer, for RPG Maker XP / VX / VX Ace data.

The data is loaded into plain Python nodes that keep everything needed to
write it back byte for byte (instance-variable order, string encodings,
user-defined dumps such as Table/Color/Tone, shared object links), so only
the strings we change differ in the output. Symbol and object link tables
are rebuilt on write the way Ruby does it, so nodes can also be inserted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class MarshalError(Exception):
    pass


class Sym(str):
    """A Ruby symbol."""


@dataclass(eq=False)
class RString:
    data: bytes
    ivars: list | None = None  # [(Sym, node)] when wrapped with 'I' (e.g. encoding E=true)

    @property
    def text(self) -> str:
        return self.data.decode("utf-8", "replace")

    def set(self, text: str) -> None:
        self.data = text.encode("utf-8")


@dataclass(eq=False)
class RObject:
    cls: Sym
    ivars: list = field(default_factory=list)  # [(Sym, node)]

    def get(self, name: str, default=None):
        for k, v in self.ivars:
            if k == name:
                return v
        return default

    def put(self, name: str, value) -> None:
        for i, (k, _) in enumerate(self.ivars):
            if k == name:
                self.ivars[i] = (k, value)
                return
        self.ivars.append((Sym(name), value))


@dataclass(eq=False)
class RArray:
    items: list
    ivars: list | None = None


@dataclass(eq=False)
class RHash:
    pairs: list  # [(key, value)]
    default: Any = None
    has_default: bool = False
    ivars: list | None = None


@dataclass(eq=False)
class RFloat:
    raw: bytes


@dataclass(eq=False)
class RBignum:
    sign: bytes
    raw: bytes


@dataclass(eq=False)
class RUserDef:  # 'u': class with _dump/_load (Table, Color, Tone, Rect)
    cls: Sym
    data: bytes
    ivars: list | None = None


@dataclass(eq=False)
class RUserMarshal:  # 'U'
    cls: Sym
    value: Any


@dataclass(eq=False)
class RStruct:
    cls: Sym
    members: list  # [(Sym, node)]
    ivars: list | None = None


@dataclass(eq=False)
class RRegexp:
    source: bytes
    options: int
    ivars: list | None = None


@dataclass(eq=False)
class RClassRef:
    kind: bytes  # b"c", b"m" or b"M"
    name: bytes


@dataclass(eq=False)
class RData:
    cls: Sym
    value: Any


@dataclass(eq=False)
class RExtended:  # 'e': object extended with a module (wrapper, not an object itself)
    module: Sym
    inner: Any


@dataclass(eq=False)
class RUClass:  # 'C': instance of a subclass of String/Array/Hash/Regexp
    cls: Sym
    inner: Any


_REGISTERED = (RString, RObject, RArray, RHash, RFloat, RBignum, RUserDef, RUserMarshal, RStruct, RRegexp, RClassRef, RData)


# ======================================================================================
class _Reader:
    def __init__(self, data: bytes):
        self.d = data
        self.p = 0
        self.syms: list[Sym] = []
        self.objs: list = []

    def byte(self) -> int:
        if self.p >= len(self.d):
            raise MarshalError("unexpected end of data")
        b = self.d[self.p]
        self.p += 1
        return b

    def bytes_(self, n: int) -> bytes:
        if n < 0 or self.p + n > len(self.d):
            raise MarshalError("unexpected end of data")
        b = self.d[self.p : self.p + n]
        self.p += n
        return b

    def long(self) -> int:
        c = self.byte()
        if c == 0:
            return 0
        if c > 127:
            c -= 256
        if 4 < c < 128:
            return c - 5
        if -129 < c < -4:
            return c + 5
        if c > 0:
            return int.from_bytes(self.bytes_(c), "little")
        n = -c
        return int.from_bytes(self.bytes_(n), "little") - (1 << (8 * n))

    def blob(self) -> bytes:
        return self.bytes_(self.long())

    def symbol(self) -> Sym:
        t = self.byte()
        if t == 0x3A:  # ':'
            s = Sym(self.blob().decode("utf-8", "surrogateescape"))
            self.syms.append(s)
            return s
        if t == 0x3B:  # ';'
            return self.syms[self.long()]
        if t == 0x49:  # 'I' symbol with encoding ivars
            s = self.symbol()
            for _ in range(self.long()):
                self.symbol()
                self.read()
            return s
        raise MarshalError(f"expected symbol, got {chr(t)!r}")

    def reg(self, node):
        self.objs.append(node)
        return node

    def ivar_list(self) -> list:
        return [(self.symbol(), self.read()) for _ in range(self.long())]

    def read(self):
        t = chr(self.byte())
        if t == "0":
            return None
        if t == "T":
            return True
        if t == "F":
            return False
        if t == "i":
            return self.long()
        if t in ":;":
            self.p -= 1
            return self.symbol()
        if t == "@":
            idx = self.long()
            if idx >= len(self.objs):
                raise MarshalError("bad object link")
            return self.objs[idx]
        if t == "I":
            node = self.read()
            ivars = self.ivar_list()
            target = node.inner if isinstance(node, (RExtended, RUClass)) else node
            if not hasattr(target, "ivars") or isinstance(target, RObject):
                raise MarshalError("ivars on unsupported node")
            target.ivars = ivars
            return node
        if t == '"':
            node = self.reg(RString(b""))
            node.data = self.blob()
            return node
        if t == "f":
            return self.reg(RFloat(self.blob()))
        if t == "l":
            sign = self.bytes_(1)
            n = self.long()
            return self.reg(RBignum(sign, self.bytes_(n * 2)))
        if t == "[":
            node = self.reg(RArray([]))
            n = self.long()
            node.items = [self.read() for _ in range(n)]
            return node
        if t in "{}":
            node = self.reg(RHash([]))
            n = self.long()
            node.pairs = [(self.read(), self.read()) for _ in range(n)]
            if t == "}":
                node.has_default = True
                node.default = self.read()
            return node
        if t == "o":
            cls = self.symbol()
            node = self.reg(RObject(cls))
            node.ivars = self.ivar_list()
            return node
        if t == "u":
            cls = self.symbol()
            return self.reg(RUserDef(cls, self.blob()))
        if t == "U":
            cls = self.symbol()
            node = self.reg(RUserMarshal(cls, None))
            node.value = self.read()
            return node
        if t == "S":
            cls = self.symbol()
            node = self.reg(RStruct(cls, []))
            node.members = self.ivar_list()
            return node
        if t == "/":
            src = self.blob()
            return self.reg(RRegexp(src, self.byte()))
        if t in "cmM":
            return self.reg(RClassRef(t.encode(), self.blob()))
        if t == "d":
            cls = self.symbol()
            node = self.reg(RData(cls, None))
            node.value = self.read()
            return node
        if t == "e":
            mod = self.symbol()
            return RExtended(mod, self.read())
        if t == "C":
            cls = self.symbol()
            return RUClass(cls, self.read())
        raise MarshalError(f"unsupported Marshal type {t!r} at {self.p - 1}")


def loads(data: bytes):
    if data[:2] != b"\x04\x08":
        raise MarshalError("not Ruby Marshal 4.8 data")
    r = _Reader(data)
    r.p = 2
    node = r.read()
    return node


# ======================================================================================
class _Writer:
    def __init__(self):
        self.out = bytearray(b"\x04\x08")
        self.syms: dict[str, int] = {}
        self.objs: dict[int, int] = {}
        self.keep: list = []  # keep nodes alive so ids stay unique

    def long(self, v: int) -> None:
        """Ruby's w_long."""
        o = self.out
        if v == 0:
            o.append(0)
        elif 0 < v < 123:
            o.append(v + 5)
        elif -124 < v < 0:
            o.append((v - 5) & 0xFF)
        else:
            buf = []
            x = v
            for i in range(1, 5):
                buf.append(x & 0xFF)
                x >>= 8
                if x == 0:
                    o.append(i)
                    break
                if x == -1:
                    o.append((-i) & 0xFF)
                    break
            else:
                raise MarshalError("integer out of range")
            o += bytes(buf)

    def blob(self, b: bytes) -> None:
        self.long(len(b))
        self.out += b

    def symbol(self, s: str) -> None:
        if s in self.syms:
            self.out.append(0x3B)
            self.long(self.syms[s])
        else:
            self.syms[s] = len(self.syms)
            self.out.append(0x3A)
            self.blob(s.encode("utf-8", "surrogateescape"))

    def ivar_list(self, ivars) -> None:
        self.long(len(ivars))
        for k, v in ivars:
            self.symbol(k)
            self.write(v)

    def write(self, node) -> None:
        o = self.out
        if node is None:
            o += b"0"
            return
        if node is True:
            o += b"T"
            return
        if node is False:
            o += b"F"
            return
        if isinstance(node, Sym):
            self.symbol(node)
            return
        if isinstance(node, int):
            if not -(1 << 30) <= node < (1 << 30):
                raise MarshalError("integer too large for a Fixnum")
            o += b"i"
            self.long(node)
            return
        if isinstance(node, RExtended):
            o += b"e"
            self.symbol(node.module)
            self.write(node.inner)
            return
        if isinstance(node, RUClass):
            ivars = getattr(node.inner, "ivars", None)
            if ivars and not isinstance(node.inner, RObject) and id(node.inner) not in self.objs:
                o += b"I"
            o += b"C"
            self.symbol(node.cls)
            self._body(node.inner, wrap_ivars=False)
            if ivars and not isinstance(node.inner, RObject):
                self.ivar_list(ivars)
            return
        if not isinstance(node, _REGISTERED):
            raise MarshalError(f"cannot write {type(node).__name__}")
        self._body(node, wrap_ivars=True)

    def _body(self, node, wrap_ivars: bool) -> None:
        o = self.out
        if id(node) in self.objs:
            o += b"@"
            self.long(self.objs[id(node)])
            return
        ivars = getattr(node, "ivars", None) if not isinstance(node, RObject) else None
        if wrap_ivars and ivars:
            o += b"I"
        self.objs[id(node)] = len(self.objs)
        self.keep.append(node)
        if isinstance(node, RString):
            o += b'"'
            self.blob(node.data)
        elif isinstance(node, RFloat):
            o += b"f"
            self.blob(node.raw)
        elif isinstance(node, RBignum):
            o += b"l" + node.sign
            self.long(len(node.raw) // 2)
            o += node.raw
        elif isinstance(node, RArray):
            o += b"["
            self.long(len(node.items))
            for it in node.items:
                self.write(it)
        elif isinstance(node, RHash):
            o += b"}" if node.has_default else b"{"
            self.long(len(node.pairs))
            for k, v in node.pairs:
                self.write(k)
                self.write(v)
            if node.has_default:
                self.write(node.default)
        elif isinstance(node, RObject):
            o += b"o"
            self.symbol(node.cls)
            self.ivar_list(node.ivars)
        elif isinstance(node, RUserDef):
            o += b"u"
            self.symbol(node.cls)
            self.blob(node.data)
        elif isinstance(node, RUserMarshal):
            o += b"U"
            self.symbol(node.cls)
            self.write(node.value)
        elif isinstance(node, RStruct):
            o += b"S"
            self.symbol(node.cls)
            self.ivar_list(node.members)
        elif isinstance(node, RRegexp):
            o += b"/"
            self.blob(node.source)
            o.append(node.options)
        elif isinstance(node, RClassRef):
            o += node.kind
            self.blob(node.name)
        elif isinstance(node, RData):
            o += b"d"
            self.symbol(node.cls)
            self.write(node.value)
        if wrap_ivars and ivars:
            self.ivar_list(ivars)


def dumps(node) -> bytes:
    w = _Writer()
    w.write(node)
    return bytes(w.out)


# -- helpers for building nodes ------------------------------------------------------------
def rstring(text: str, like: RString | None = None) -> RString:
    """New string node, with the same encoding marker as ``like`` (UTF-8 'E' by default)."""
    ivars = None
    if like is not None:
        ivars = list(like.ivars) if like.ivars else None
    else:
        ivars = [(Sym("E"), True)]
    return RString(text.encode("utf-8"), ivars)


__all__ = [
    "MarshalError", "RArray", "RBignum", "RClassRef", "RData", "RExtended", "RFloat", "RHash", "RObject",
    "RRegexp", "RString", "RStruct", "RUClass", "RUserDef", "RUserMarshal", "Sym", "dumps", "loads", "rstring",
]
