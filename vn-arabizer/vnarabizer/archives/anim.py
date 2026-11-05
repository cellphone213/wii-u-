"""Anim / Crowd (SIS Planning) script files: ``*_sce.dat`` and ``*_define.dat``.

Container (both files)::

    magic (4 bytes) | initial key (16 bytes) | body XOR running key

The key starts as the 16 bytes in the header and is changed after every 16
bytes, depending on the plain-text byte at position 14 of that block. The
cipher is a port of toto's ``Anim`` handler (kenkyuuka/toto, MIT licence).

``*_sce.dat`` body (layout as documented by FEW_Engine)::

    0  int32  offset of the label section
    4  int32  offset of the string table
    8  int32  offset of the jump-label section (0 when unused)
    12 ...    bytecode, label section
    [4] ...   string table: Shift-JIS strings, each ending in 0x00, plus padding
              zeros, up to the end of the file

Instructions name strings by their index in the table, never by byte offset,
and the table is the last section. So a string can be made longer or shorter
as long as the number of strings and their order stay the same: splitting
the table on 0x00 and joining it again keeps every index.

``*_define.dat`` body: instructions back to back (int32 opcode, int32
arguments, inline 0x00-terminated strings) with no offsets, so inline
strings can change length too.
"""

from __future__ import annotations

import re
import struct
import unicodedata
from dataclasses import dataclass

HEADER = 20


class AnimError(Exception):
    pass


def _switch_key(k: bytearray, ch: int) -> None:
    t = ch
    m = ch & 7
    if m == 0:
        k[0] = (k[0] + t) & 0xFF
        k[3] = (k[3] + t + 2) & 0xFF
        k[4] = (k[2] + t + 11) & 0xFF
        k[8] = (k[6] + 7) & 0xFF
    elif m == 1:
        k[2] = (k[9] + k[10]) & 0xFF
        k[6] = (k[7] + k[15]) & 0xFF
        k[8] = (k[8] + k[1]) & 0xFF
        k[15] = (k[5] + k[3]) & 0xFF
    elif m == 2:
        k[1] = (k[1] + k[2]) & 0xFF
        k[5] = (k[5] + k[6]) & 0xFF
        k[7] = (k[7] + k[8]) & 0xFF
        k[10] = (k[10] + k[11]) & 0xFF
    elif m == 3:
        k[9] = (k[2] + k[1]) & 0xFF
        k[11] = (k[6] + k[5]) & 0xFF
        k[12] = (k[8] + k[7]) & 0xFF
        k[13] = (k[11] + k[10]) & 0xFF
    elif m == 4:
        k[0] = (k[1] + 111) & 0xFF
        k[3] = (k[4] + 71) & 0xFF
        k[4] = (k[5] + 17) & 0xFF
        k[14] = (k[15] + 64) & 0xFF
    elif m == 5:
        k[2] = (k[2] + k[10]) & 0xFF
        k[4] = (k[5] + k[12]) & 0xFF
        k[6] = (k[8] + k[14]) & 0xFF
        k[8] = (k[11] + k[0]) & 0xFF
    elif m == 6:
        k[9] = (k[11] + k[1]) & 0xFF
        k[11] = (k[13] + k[3]) & 0xFF
        k[13] = (k[15] + k[5]) & 0xFF
        k[15] = (k[9] + k[7]) & 0xFF
        k[1] = (k[9] + k[5]) & 0xFF
        k[2] = (k[10] + k[6]) & 0xFF
        k[3] = (k[11] + k[7]) & 0xFF
        k[4] = (k[12] + k[8]) & 0xFF
    else:
        k[1] = (k[9] + k[5]) & 0xFF
        k[2] = (k[10] + k[6]) & 0xFF
        k[3] = (k[11] + k[7]) & 0xFF
        k[4] = (k[12] + k[8]) & 0xFF


def _xor16(block: bytes, key: bytes) -> bytes:
    n = len(block)
    return (int.from_bytes(block, "little") ^ int.from_bytes(key[:n], "little")).to_bytes(n, "little")


def decrypt(raw: bytes) -> bytes:
    """Body of an encrypted file (header removed)."""
    if len(raw) < HEADER:
        raise AnimError("file too short")
    key = bytearray(raw[4:HEADER])
    body = raw[HEADER:]
    out = bytearray()
    for pos in range(0, len(body), 16):
        plain = _xor16(body[pos : pos + 16], key)
        out += plain
        if len(plain) == 16:
            _switch_key(key, plain[14])
    return bytes(out)


def encrypt(body: bytes, header: bytes) -> bytes:
    """Encrypt ``body`` with the magic and key of ``header`` (the first 20 bytes of the original)."""
    if len(header) < HEADER:
        raise AnimError("bad header")
    key = bytearray(header[4:HEADER])
    out = bytearray(header[:HEADER])
    for pos in range(0, len(body), 16):
        plain = body[pos : pos + 16]
        out += _xor16(plain, key)
        if len(plain) == 16:
            _switch_key(key, plain[14])
    return bytes(out)


# -- *_sce.dat ----------------------------------------------------------------------------
@dataclass
class SceScript:
    header: bytes  # magic + key
    code: bytes  # everything before the string table (offsets, bytecode, labels)
    strings: list[bytes]  # the table split on 0x00 (trailing padding shows up as empty strings)

    @classmethod
    def load(cls, raw: bytes) -> "SceScript":
        body = decrypt(raw)
        if len(body) < 12:
            raise AnimError("script too short")
        labels, table, jumps = struct.unpack_from("<III", body, 0)
        if not (12 <= table <= len(body)) or labels > table or (jumps and jumps < table):
            raise AnimError("not an Anim/Crowd script (bad section offsets)")
        if jumps:
            # A jump-label section after the strings would move if the strings changed size.
            raise AnimError("this script has a jump-label section after the strings (not supported)")
        return cls(raw[:HEADER], body[:table], body[table:].split(b"\0"))

    def body(self) -> bytes:
        return self.code + b"\0".join(self.strings)

    def dump(self) -> bytes:
        return encrypt(self.body(), self.header)


# -- *_define.dat -------------------------------------------------------------------------
_JP_RUN = re.compile(r"[぀-ゟ゠-ヿ一-鿿]{2,}")


@dataclass
class DefineFile:
    header: bytes
    pieces: list[bytes | str]  # raw bytes, or a text string (stored 0x00-terminated)

    @classmethod
    def load(cls, raw: bytes) -> "DefineFile":
        data = decrypt(raw)
        pieces: list[bytes | str] = []
        other = bytearray()
        i = 0
        n = len(data)
        while i < n:
            if data[i] == 0:
                other.append(0)
                i += 1
                continue
            end = data.find(b"\0", i)
            if end < 0:
                end = n
            text = None
            # Inline strings start right after a 0x00 (the end of an int32 argument).
            if i == 0 or data[i - 1] == 0:
                try:
                    text = data[i:end].decode("cp932")
                except UnicodeDecodeError:
                    text = None
            if (
                text
                and end < n
                and _JP_RUN.search(text)
                and not any(c != "\t" and unicodedata.category(c) == "Cc" for c in text)
            ):
                if other:
                    pieces.append(bytes(other))
                    other = bytearray()
                pieces.append(text)
                i = end + 1  # the terminator is written back with the text
            else:
                other += data[i:end]
                i = end
        if other:
            pieces.append(bytes(other))
        return cls(raw[:HEADER], pieces)

    def texts(self) -> list[str]:
        return [p for p in self.pieces if isinstance(p, str)]

    def body(self, encode=lambda s: s.encode("cp932")) -> bytes:
        out = bytearray()
        for p in self.pieces:
            if isinstance(p, str):
                b = encode(p)
                if b"\0" in b:
                    raise AnimError("encoded text contains a 0x00 byte")
                out += b + b"\0"
            else:
                out += p
        return bytes(out)

    def dump(self, encode=lambda s: s.encode("cp932")) -> bytes:
        return encrypt(self.body(encode), self.header)
