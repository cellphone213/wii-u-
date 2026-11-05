"""Shift-JIS "tunneling", compatible with VNTextPatch/VNTextProxy.

Characters Shift-JIS cannot hold (Arabic letters) are written as unused
two-byte codes (a valid lead byte followed by a byte below 0x40). The list of
original characters, in allocation order, is saved as ``sjis_ext.bin``
(UTF-16LE). VNTextProxy reads that file and draws the real characters.

Port of VNTextPatch.Shared.Util.SjisTunnelEncoding (MIT, arcusmaximus).
"""

from __future__ import annotations

from pathlib import Path

_LIMIT = 0x3B * 0x3A


class SjisTunnel:
    def __init__(self, table: bytes = b""):
        self.mapping: dict[str, int] = {}
        for i in range(0, len(table) - 1, 2):
            self._tunnel_char(chr(table[i] | (table[i + 1] << 8)))

    def _tunnel_char(self, ch: str) -> int:
        code = self.mapping.get(ch)
        if code is not None:
            return code
        if 0xD800 <= ord(ch) <= 0xDFFF:
            raise ValueError("surrogate characters are not supported")
        idx = len(self.mapping)
        if idx >= _LIMIT:
            raise ValueError("Shift-JIS tunnel limit exceeded (too many different characters)")
        high_idx, low_idx = divmod(idx, 0x3A)
        high = 0x81 + high_idx if high_idx < 0x1F else 0xE0 + (high_idx - 0x1F)
        low = 1 + low_idx
        for avoid in (0x09, 0x0A, 0x0D, 0x20, 0x2C):  # \t \n \r space comma, in this order
            if low >= avoid:
                low += 1
        code = (high << 8) | low
        self.mapping[ch] = code
        return code

    @staticmethod
    def _sjis(ch: str) -> bytes | None:
        try:
            b = ch.encode("cp932")
        except UnicodeEncodeError:
            return None
        if b and b[0] >= 0xF0:  # user-defined area: treat as unsupported, like VNTextPatch
            return None
        return b

    def encode(self, text: str) -> bytes:
        out = bytearray()
        for ch in text:
            b = None if ch in self.mapping else self._sjis(ch)
            if b is None:
                code = self._tunnel_char(ch)
                out += bytes([code >> 8, code & 0xFF])
            else:
                out += b
        return bytes(out)

    def decode(self, data: bytes) -> str:
        reverse = {v: k for k, v in self.mapping.items()}
        out = []
        i = 0
        while i < len(data):
            hi = data[i]
            if (0x81 <= hi < 0xA0 or 0xE0 <= hi < 0xFD) and i + 1 < len(data):
                lo = data[i + 1]
                if lo < 0x40:
                    out.append(reverse.get((hi << 8) | lo, "?"))
                else:
                    out.append(bytes([hi, lo]).decode("cp932", "replace"))
                i += 2
            else:
                out.append(bytes([hi]).decode("cp932", "replace"))
                i += 1
        return "".join(out)

    def table(self) -> bytes:
        out = bytearray()
        for ch in self.mapping:
            out += bytes([ord(ch) & 0xFF, ord(ch) >> 8])
        return bytes(out)

    def save(self, path: Path) -> None:
        Path(path).write_bytes(self.table())
