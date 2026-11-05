"""KiriKiri XP3 archives.

Reading supports unencrypted archives (zlib-compressed or raw index and
segments). Encrypted games (per-game "cxdec" schemes, hashed names) cannot
be read here; their scripts must be extracted with GARbro or KirikiriTools.

Writing produces an unencrypted patch archive in the same layout as
KirikiriTools' Xp3Pack: every file hash is zero, which tells the
KirikiriUnencryptedArchive helper DLL to skip the game's decryption for
those files. The engine loads ``patch.xp3``, ``patch2.xp3`` ... on top of
``data.xp3``.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

MAGIC = b"XP3\r\n \n\x1a\x8b\x67\x01"


class Xp3Error(Exception):
    pass


@dataclass
class Xp3Entry:
    name: str
    encrypted: bool
    size: int
    segments: list[tuple[bool, int, int, int]] = field(default_factory=list)  # (compressed, offset, size, packed)


def _read_index(f) -> bytes:
    flag = f.read(1)[0]
    if flag & 0x07 == 1:
        packed, size = struct.unpack("<QQ", f.read(16))
        return zlib.decompress(f.read(packed))
    if flag & 0x07 == 0:
        size = struct.unpack("<Q", f.read(8))[0]
        return f.read(size)
    raise Xp3Error(f"unknown index flag {flag}")


class Xp3Archive:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.entries: list[Xp3Entry] = []
        with open(self.path, "rb") as f:
            if f.read(11) != MAGIC:
                raise Xp3Error("not an XP3 archive")
            offset = struct.unpack("<Q", f.read(8))[0]
            if offset == 0x17:  # version 2 header: a cushion pointing at the real index
                f.seek(offset)
                f.read(1 + 8)  # 0x80 flag + reserved
                offset = struct.unpack("<Q", f.read(8))[0]
            f.seek(offset)
            index = _read_index(f)
        self._parse(index)

    def _parse(self, index: bytes) -> None:
        pos = 0
        while pos + 12 <= len(index):
            tag = index[pos : pos + 4]
            size = struct.unpack_from("<Q", index, pos + 4)[0]
            body = index[pos + 12 : pos + 12 + size]
            pos += 12 + size
            if tag != b"File":
                continue
            entry = Xp3Entry("", False, 0)
            p = 0
            while p + 12 <= len(body):
                ctag = body[p : p + 4]
                csize = struct.unpack_from("<Q", body, p + 4)[0]
                chunk = body[p + 12 : p + 12 + csize]
                p += 12 + csize
                if ctag == b"info":
                    flags, orig, packed, nlen = struct.unpack_from("<IQQH", chunk, 0)
                    entry.encrypted = bool(flags & 0x80000000)
                    entry.size = orig
                    entry.name = chunk[22 : 22 + nlen * 2].decode("utf-16-le", "replace")
                elif ctag == b"segm":
                    for s in range(0, len(chunk) - 27, 28):
                        flags, off, orig, packed = struct.unpack_from("<IQQQ", chunk, s)
                        entry.segments.append((bool(flags & 7), off, orig, packed))
            if entry.name:
                self.entries.append(entry)

    def names(self) -> list[str]:
        return [e.name for e in self.entries]

    def read(self, entry: Xp3Entry) -> bytes:
        out = bytearray()
        with open(self.path, "rb") as f:
            for compressed, off, orig, packed in entry.segments:
                f.seek(off)
                data = f.read(packed)
                out += zlib.decompress(data) if compressed else data
        return bytes(out)


def write_xp3(path: Path, files: list[tuple[str, bytes]], compress: bool = True) -> None:
    """Write an unencrypted XP3 archive with zero hashes (Xp3Pack layout)."""
    tmp = Path(str(path) + ".tmp")
    index = bytearray()
    with open(tmp, "wb") as f:
        f.write(MAGIC)
        f.write(struct.pack("<Q", 0))  # index offset, patched below
        for name, data in files:
            offset = f.tell()
            stored = zlib.compress(data, 9) if compress else data
            if compress and len(stored) >= len(data):
                stored, flag = data, 0
            else:
                flag = 1 if compress else 0
            f.write(stored)
            name16 = name.encode("utf-16-le")
            info = struct.pack("<IQQH", 0, len(data), len(stored), len(name) if name.isascii() else len(name16) // 2) + name16
            segm = struct.pack("<IQQQ", flag, offset, len(data), len(stored))
            adlr = struct.pack("<I", 0)  # zero hash: marker for KirikiriUnencryptedArchive
            body = (
                b"info" + struct.pack("<Q", len(info)) + info
                + b"segm" + struct.pack("<Q", len(segm)) + segm
                + b"adlr" + struct.pack("<Q", len(adlr)) + adlr
            )
            index += b"File" + struct.pack("<Q", len(body)) + body
        index_offset = f.tell()
        packed = zlib.compress(bytes(index), 9)
        f.write(b"\x01" + struct.pack("<QQ", len(packed), len(index)) + packed)
        f.seek(len(MAGIC))
        f.write(struct.pack("<Q", index_offset))
    tmp.replace(path)
