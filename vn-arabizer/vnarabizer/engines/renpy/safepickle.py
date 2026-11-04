"""Load Ren'Py pickles (.rpyc scripts, .rpa indexes) without running game code.

Ren'Py stores compiled scripts as Python pickles. Unpickling normally imports
classes and may call arbitrary functions, which is unsafe for files from an
untrusted game. This unpickler never imports anything: every class reference
becomes an inert ``Fake`` placeholder that only records the data it is given.
Only a handful of plain builtin containers are created for real.
"""

from __future__ import annotations

import io
import pickle
import zlib

MAX_DECOMPRESSED = 512 * 1024 * 1024  # refuse absurd sizes (zip bombs)


class FakeBase:
    """Placeholder instance for any pickled object."""

    _module = "?"
    _name = "?"

    def __init__(self, *args, **kwargs):
        self._args = args
        if kwargs:
            self.__dict__.update(kwargs)

    # Protocol 2+ object construction.
    @classmethod
    def _new(cls, *args):
        obj = cls.__new__(cls)
        obj._args = args
        return obj

    def __setstate__(self, state):
        if isinstance(state, dict):
            self.__dict__.update({_k(k): v for k, v in state.items()})
        elif isinstance(state, tuple) and all(d is None or isinstance(d, dict) for d in state):
            # (dict, slots) or (None, slots)
            for d in state:
                if d:
                    self.__dict__.update({_k(k): v for k, v in d.items()})
        else:
            self._state = state

    # Some pickled classes subclass list/dict and receive APPEND/SETITEM ops.
    def append(self, item):
        self.__dict__.setdefault("_items", []).append(item)

    def extend(self, items):
        self.__dict__.setdefault("_items", []).extend(items)

    def __setitem__(self, key, value):
        self.__dict__.setdefault("_dict", {})[key] = value

    def __repr__(self):
        return f"<{self._module}.{self._name}>"

    @property
    def fake_name(self) -> str:
        return self._name


class FakeStr(str):
    """Placeholder for str subclasses such as Ren'Py's PyExpr."""

    _module = "?"
    _name = "?"

    def __new__(cls, value="", *args):
        if isinstance(value, bytes):
            value = value.decode("utf-8", "replace")
        return str.__new__(cls, value)

    def __init__(self, *args):
        pass

    def __setstate__(self, state):
        if isinstance(state, dict):
            self.__dict__.update({_k(k): v for k, v in state.items()})


def _k(k):
    return k.decode("utf-8", "replace") if isinstance(k, bytes) else k


_fake_cache: dict[tuple[str, str], type] = {}


def fake_class(module: str, name: str) -> type:
    key = (module, name)
    cls = _fake_cache.get(key)
    if cls is None:
        base = FakeStr if name in ("PyExpr",) else FakeBase
        cls = type(name, (base,), {"_module": module, "_name": name, "__module__": "vnarabizer.fake." + module})
        _fake_cache[key] = cls
    return cls


def _newobj(cls, *args):
    if isinstance(cls, type) and issubclass(cls, FakeStr):
        return cls(*args)
    if isinstance(cls, type) and issubclass(cls, FakeBase):
        return cls._new(*args)
    if cls in _SAFE_TYPES.values():
        return cls.__new__(cls, *args) if args else cls()
    raise pickle.UnpicklingError(f"refusing to construct {cls!r}")


def _reconstructor(cls, base, state):
    if isinstance(cls, type) and issubclass(cls, (FakeBase, FakeStr)):
        obj = _newobj(cls, state) if issubclass(cls, FakeStr) and state is not None else _newobj(cls)
        return obj
    if cls in _SAFE_TYPES.values():
        return cls(state) if state is not None else cls()
    raise pickle.UnpicklingError(f"refusing to reconstruct {cls!r}")


_SAFE_TYPES = {
    "set": set,
    "frozenset": frozenset,
    "list": list,
    "dict": dict,
    "tuple": tuple,
    "object": object,
    "str": str,
    "unicode": str,
    "bytes": bytes,
    "bytearray": bytearray,
    "int": int,
    "long": int,
    "float": float,
    "complex": complex,
    "bool": bool,
}


class SafeUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        if module in ("builtins", "__builtin__") and name in _SAFE_TYPES:
            return _SAFE_TYPES[name]
        if module in ("copyreg", "copy_reg"):
            if name == "__newobj__" or name == "__newobj_ex__":
                return _newobj
            if name == "_reconstructor":
                return _reconstructor
        if module == "collections" and name in ("OrderedDict", "defaultdict"):
            return dict if name == "OrderedDict" else _defaultdict
        return fake_class(module, name)


def _defaultdict(*args):
    return {}


def loads(data: bytes):
    """Unpickle ``data`` safely. Python 2 strings are decoded as UTF-8."""
    up = SafeUnpickler(io.BytesIO(data), encoding="utf-8", errors="surrogateescape")
    return up.load()


def decompress(data: bytes) -> bytes:
    d = zlib.decompressobj()
    out = d.decompress(data, MAX_DECOMPRESSED)
    if d.unconsumed_tail:
        raise ValueError("compressed data too large")
    return out
