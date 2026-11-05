"""RPG Maker XP / VX / VX Ace encrypted archives (Game.rgssad, Game.rgss2a, Game.rgss3a).

Version 1 (XP, VX): entries follow one another; each header (name length,
name, size) is XORed with a running key (key = key * 7 + 3 per step,
starting at 0xDEADCAFE); the data of an entry is XORed with a keystream
that starts from the running key at that point.

Version 3 (VX Ace): an index of (offset, size, file key, name length, name)
XORed with ``seed * 9 + 3``; each file's data is XORed with a keystream that
starts from its own file key.

Keystream: one 32-bit little-endian word per 4 bytes, key = key * 7 + 3
after each word.

Rebuilding keeps every unchanged file's encrypted bytes as they are, so even
a large archive is rewritten quickly.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

MAGIC = b"RGSSAD\0"
_M = 0xFFFFFFFF
_CHUNK = 4 * 1024 * 1024


class RgssError(Exception):
    pass


def _next(k: int) -> int:
    return (k * 7 + 3) & _M


def crypt(data: bytes, key: int) -> bytes:
    """XOR ``data`` with the RGSS keystream starting at ``key`` (encrypts and decrypts)."""
    n = (len(data) + 3) // 4
    words = []
    k = key
    for _ in range(n):
        words.append(k)
        k = (k * 7 + 3) & _M
    stream = struct.pack(f"<{n}I", *words)[: len(data)]
    return (int.from_bytes(data, "little") ^ int.from_bytes(stream, "little")).to_bytes(len(data), "little") if data else b""


@dataclass
class RgssEntry:
    name: str  # as stored, with backslashes
    offset: int
    size: int
    key: int
    raw_name: bytes


class RgssArchive:
    def __init__(self, path: Path):
        self.path = Path(path)
        with open(self.path, "rb") as f:
            head = f.read(8)
            if head[:7] != MAGIC or head[7] not in (1, 3):
                raise RgssError("not an RGSS archive")
            self.version = head[7]
            self.entries: list[RgssEntry] = []
            if self.version == 3:
                self._read_v3(f)
            else:
                self._read_v1(f)

    def _read_v1(self, f) -> None:
        key = 0xDEADCAFE
        size = self.path.stat().st_size
        while f.tell() < size:
            raw = f.read(4)
            if len(raw) < 4:
                break
            n = struct.unpack("<I", raw)[0] ^ key
            key = _next(key)
            if n > 4096:
                raise RgssError("bad entry name length")
            enc = f.read(n)
            name = bytearray()
            for b in enc:
                name.append(b ^ (key & 0xFF))
                key = _next(key)
            fsize = struct.unpack("<I", f.read(4))[0] ^ key
            key = _next(key)
            off = f.tell()
            if off + fsize > size:
                raise RgssError("entry outside the archive")
            self.entries.append(RgssEntry(bytes(name).decode("utf-8", "replace"), off, fsize, key, bytes(name)))
            f.seek(fsize, 1)

    def _read_v3(self, f) -> None:
        self.seed = struct.unpack("<I", f.read(4))[0]
        key = (self.seed * 9 + 3) & _M
        self.index_key = key
        size = self.path.stat().st_size
        while True:
            raw = f.read(16)
            if len(raw) < 16:
                raise RgssError("truncated index")
            off, fsize, fkey, n = (v ^ key for v in struct.unpack("<4I", raw))
            if off == 0:
                break
            if n > 4096:
                raise RgssError("bad entry name length")
            enc = f.read(n)
            kb = struct.pack("<I", key)
            name = bytes(b ^ kb[i % 4] for i, b in enumerate(enc))
            if off + fsize > size:
                raise RgssError("entry outside the archive")
            self.entries.append(RgssEntry(name.decode("utf-8", "replace"), off, fsize, fkey, name))
        self.index_end = f.tell()

    # -- access ----------------------------------------------------------------------------
    def find(self, name: str) -> RgssEntry | None:
        want = name.replace("/", "\\").lower()
        for e in self.entries:
            if e.name.lower() == want:
                return e
        return None

    def read(self, e: RgssEntry | str) -> bytes:
        if isinstance(e, str):
            found = self.find(e)
            if found is None:
                raise KeyError(e)
            e = found
        with open(self.path, "rb") as f:
            f.seek(e.offset)
            return crypt(f.read(e.size), e.key)

    # -- writing ---------------------------------------------------------------------------
    def write(self, dest: Path, replacements: dict[str, bytes], progress: Callable[[float], None] = lambda f: None) -> None:
        """Write a copy with files (by archive name) replaced. New names are not supported."""
        dest = Path(dest)
        if dest.resolve() == self.path.resolve():
            raise RgssError("refusing to overwrite the archive being read")
        repl = {k.replace("/", "\\").lower(): v for k, v in replacements.items()}
        unknown = set(repl) - {e.name.lower() for e in self.entries}
        if unknown:
            raise RgssError(f"not in the archive: {', '.join(sorted(unknown))}")
        if self.version == 3:
            self._write_v3(dest, repl, progress)
        else:
            self._write_v1(dest, repl, progress)

    def _copy(self, src, out, offset: int, size: int) -> None:
        src.seek(offset)
        left = size
        while left > 0:
            chunk = src.read(min(_CHUNK, left))
            if not chunk:
                raise RgssError("unexpected end of archive")
            out.write(chunk)
            left -= len(chunk)

    def _write_v1(self, dest, repl, progress) -> None:
        key = 0xDEADCAFE
        total = max(1, len(self.entries))
        with open(self.path, "rb") as src, open(dest, "wb") as out:
            out.write(MAGIC + b"\x01")
            for i, e in enumerate(self.entries):
                new = repl.get(e.name.lower())
                size = len(new) if new is not None else e.size
                out.write(struct.pack("<I", len(e.raw_name) ^ key))
                key = _next(key)
                enc = bytearray()
                for b in e.raw_name:
                    enc.append(b ^ (key & 0xFF))
                    key = _next(key)
                out.write(enc)
                out.write(struct.pack("<I", size ^ key))
                key = _next(key)
                if key != e.key:
                    raise RgssError("key sequence mismatch")
                if new is not None:
                    out.write(crypt(new, key))
                else:
                    self._copy(src, out, e.offset, e.size)
                progress((i + 1) / total)

    def _write_v3(self, dest, repl, progress) -> None:
        key = self.index_key
        # Keep every file where it is; changed files go to the end of the archive.
        file_size = self.path.stat().st_size
        end = max([self.index_end] + [e.offset + e.size for e in self.entries])
        new_pos = max(end, file_size)
        layout = []
        for e in self.entries:
            new = repl.get(e.name.lower())
            if new is None:
                layout.append((e, e.offset, e.size, None))
            else:
                layout.append((e, new_pos, len(new), crypt(new, e.key)))
                new_pos += len(new)
        with open(self.path, "rb") as src, open(dest, "wb") as out:
            self._copy(src, out, 0, file_size)
            kb = struct.pack("<I", key)
            pos = 12
            for e, off, size, _ in layout:
                out.seek(pos)
                out.write(struct.pack("<4I", off ^ key, size ^ key, e.key ^ key, len(e.raw_name) ^ key))
                out.write(bytes(b ^ kb[i % 4] for i, b in enumerate(e.raw_name)))
                pos += 16 + len(e.raw_name)
            out.seek(max(end, file_size))
            for i, (e, off, size, data) in enumerate(layout):
                if data is not None:
                    out.write(data)
                progress((i + 1) / len(layout))


def build(dest: Path, files: list[tuple[str, bytes]], version: int = 3, seed: int = 0x1234) -> None:
    """Create an archive from scratch (used by tests and demos)."""
    out = bytearray(MAGIC + bytes([version]))
    if version == 3:
        out += struct.pack("<I", seed)
        key = (seed * 9 + 3) & _M
        kb = struct.pack("<I", key)
        index_len = sum(16 + len(n.encode()) for n, _ in files) + 16
        pos = len(out) + index_len
        body = bytearray()
        for i, (name, data) in enumerate(files):
            raw = name.replace("/", "\\").encode()
            fkey = (0x9876 + i * 0x1111) & _M
            out += struct.pack("<4I", pos ^ key, len(data) ^ key, fkey ^ key, len(raw) ^ key)
            out += bytes(b ^ kb[j % 4] for j, b in enumerate(raw))
            body += crypt(data, fkey)
            pos += len(data)
        out += struct.pack("<4I", key, key, key, key)
        out += body
    else:
        key = 0xDEADCAFE
        for name, data in files:
            raw = name.replace("/", "\\").encode()
            out += struct.pack("<I", len(raw) ^ key)
            key = _next(key)
            for b in raw:
                out.append(b ^ (key & 0xFF))
                key = _next(key)
            out += struct.pack("<I", len(data) ^ key)
            key = _next(key)
            out += crypt(data, key)
    Path(dest).write_bytes(bytes(out))
