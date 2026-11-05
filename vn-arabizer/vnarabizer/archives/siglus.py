"""SiglusEngine Scene.pck (scene archive) reading and rebuilding.

Format details follow siglus_rs (xmoezzz, MPL-2.0): siglus_assets/scene_pck.rs,
lzss.rs, keys.rs and siglus_scene_linker. This file is a re-implementation
and is likewise available under the MPL-2.0.

Layout:
  pack header (23 little-endian int32, ``header_size`` first)
  ... lists ... scene names (UTF-16) ... scene data index (offset, size) ...
  scene data: per scene  lzss(scene) XOR fixed 256-byte key XOR 16-byte game key
  (optional trailing "original source" archive, kept unchanged)

A scene (.ss) has a 33-int header; its strings are UTF-16 in a table of
(offset, length) pairs, usually XORed with (28807 * index) & 0xFFFF.
Bytecode refers to strings by index, so strings can change length freely.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

# Fixed "easy angou" table shared by Siglus games (siglus_rs keys.rs SCENE_KEY).
SCENE_KEY = bytes.fromhex(
    "70f8a6b0a1a5284fb52f48fae1e94bdeb74f62958be00380e7cf0f6b9201ebf8"
    "a288ce630438d26d8cd28876a792718f4eb68d017988830af9e92cdb67db9114"
    "d59a4e79172308960e1d15f9a5a06f5817c8a946da22fffd871242fba9b8676c"
    "916764f9d11ee450646ff20bde40e747f103cc2aad7f3421a06426986ced69f4"
    "b523086e7d92f6eb93f07a895ef9f87aafe8a948c2ac116b2b33a7400ddc7da7"
    "5bcfc831d177528d82ac41b873a54f267c0f39da5b374adea4490b7c17a343ae"
    "77066473c043a3185a0f9f024c7e8b019f2dae725413ff96ae0b3458cfe30078"
    "bee3f561e4877cfc80afc48d463a5dd036bce5607768084fbbabe27807e873bf"
)
PACK_FIELDS = 23
SCN_FIELDS = 33


class SiglusError(Exception):
    pass


def _xor(data: bytes, key: bytes) -> bytes:
    if not key:
        return data
    k = (key * (len(data) // len(key) + 1))[: len(data)]
    return bytes(a ^ b for a, b in zip(data, k))


def lzss_unpack(src: bytes) -> bytes:
    if len(src) < 8:
        raise SiglusError("lzss: input too short")
    arc_size, org_size = struct.unpack_from("<II", src, 0)
    end = min(arc_size, len(src))
    out = bytearray()
    pos = 8
    while len(out) < org_size and pos < end:
        flags = src[pos]
        pos += 1
        for _ in range(8):
            if len(out) >= org_size or pos >= end:
                break
            if flags & 1:
                out.append(src[pos])
                pos += 1
            else:
                if pos + 2 > end:
                    raise SiglusError("lzss: truncated back-reference")
                token = src[pos] | (src[pos + 1] << 8)
                pos += 2
                off, length = token >> 4, (token & 0x0F) + 2
                if off == 0 or off > len(out):
                    raise SiglusError("lzss: bad back-reference")
                start = len(out) - off
                for i in range(length):
                    if len(out) >= org_size:
                        break
                    out.append(out[start + i])
            flags >>= 1
    if len(out) != org_size:
        raise SiglusError("lzss: size mismatch")
    return bytes(out)


def lzss_pack(data: bytes) -> bytes:
    """Store everything as literals (valid LZSS the engine decodes as usual)."""
    payload = bytearray()
    for i in range(0, len(data), 8):
        group = data[i : i + 8]
        payload.append((1 << len(group)) - 1)
        payload += group
    return struct.pack("<II", 8 + len(payload), len(data)) + bytes(payload)


def _lzss_header_ok(chunk: bytes) -> bool:
    if len(chunk) < 8:
        return False
    arc, org = struct.unpack_from("<II", chunk, 0)
    return arc == len(chunk) and 0 < org < 64 * 1024 * 1024


@dataclass
class Scene:
    name: str
    data: bytes  # decoded .ss bytes
    strings: list[str] = field(default_factory=list)


def _str_key(i: int) -> int:
    return (28807 * i) & 0xFFFF


def read_strings(ss: bytes, xor: bool) -> list[str]:
    h = struct.unpack_from(f"<{SCN_FIELDS}i", ss, 0)
    idx_ofs, idx_cnt, list_ofs = h[3], h[4], h[5]
    out = []
    for i in range(idx_cnt):
        ofs, size = struct.unpack_from("<ii", ss, idx_ofs + i * 8)
        start = list_ofs + ofs * 2
        units = struct.unpack_from(f"<{size}H", ss, start) if size else ()
        if xor:
            k = _str_key(i)
            units = [u ^ k for u in units]
        out.append(struct.pack(f"<{len(units)}H", *units).decode("utf-16-le", "replace"))
    return out


def write_strings(ss: bytes, strings: list[str], xor: bool) -> bytes:
    """Append a new string table to the scene and point the header at it."""
    h = list(struct.unpack_from(f"<{SCN_FIELDS}i", ss, 0))
    if len(strings) != h[4]:
        raise SiglusError("string count changed")
    index = bytearray()
    body = bytearray()
    pos = 0
    for i, s in enumerate(strings):
        units = list(struct.unpack(f"<{len(s.encode('utf-16-le')) // 2}H", s.encode("utf-16-le")))
        if xor:
            k = _str_key(i)
            units = [u ^ k for u in units]
        index += struct.pack("<ii", pos, len(units))
        body += struct.pack(f"<{len(units)}H", *units)
        pos += len(units)
    out = bytearray(ss)
    while len(out) % 4:
        out.append(0)
    h[3] = len(out)
    out += index
    h[5] = len(out)
    out += body
    struct.pack_into(f"<{SCN_FIELDS}i", out, 0, *h)
    return bytes(out)


def _string_score(strings: list[str]) -> float:
    good = total = 0
    for s in strings:
        for ch in s:
            total += 1
            o = ord(ch)
            if (
                o in (9, 10, 13)
                or 0x20 <= o < 0x7F
                or 0x0600 <= o < 0x0700  # Arabic (our own patched files are read back too)
                or 0x2000 <= o < 0x2070
                or 0x3000 <= o < 0x3100
                or 0x4E00 <= o < 0xA000
                or 0xFB50 <= o < 0xFE00
                or 0xFE70 <= o < 0xFF00
                or 0xFF00 <= o < 0xFFF0
            ):
                good += 1
    return good / total if total else 0.0


class ScenePck:
    def __init__(self, data: bytes, key: bytes | None = None):
        self.raw = data
        self.sig = data[:8] == b"pack_scn"
        base = 8 if self.sig else 0
        self.base = base
        self.header = list(struct.unpack_from(f"<{PACK_FIELDS}i", data, base))
        if self.header[0] < PACK_FIELDS * 4:
            raise SiglusError("not a Siglus Scene.pck")
        self.exe_angou = self.header[21] != 0
        self.key = key if key and any(key) else None
        if self.exe_angou and self.key is None:
            raise SiglusError("this Scene.pck is encrypted with a per-game key")
        self.names = self._names()
        self.chunks = self._chunks()
        self.compressed = self._detect_compressed()
        self.scenes = [Scene(n, self._decode(c)) for n, c in zip(self.names, self.chunks)]
        self.string_xor = self._detect_codec()
        for s in self.scenes:
            s.strings = read_strings(s.data, self.string_xor) if s.data else []

    def _abs(self, ofs: int) -> int:
        return ofs  # offsets in the header are relative to the file start

    def _names(self) -> list[str]:
        h = self.header
        idx_ofs, cnt, list_ofs = h[13], h[14], h[15]
        out = []
        for i in range(cnt):
            ofs, size = struct.unpack_from("<ii", self.raw, idx_ofs + i * 8)
            start = list_ofs + ofs * 2
            out.append(self.raw[start : start + size * 2].decode("utf-16-le", "replace"))
        return out

    def _chunks(self) -> list[bytes]:
        h = self.header
        idx_ofs, cnt, data_ofs = h[17], max(h[18], h[20]), h[19]
        out = []
        self.data_end = data_ofs
        for i in range(cnt):
            ofs, size = struct.unpack_from("<ii", self.raw, idx_ofs + i * 8)
            start = data_ofs + ofs
            out.append(self.raw[start : start + size] if size > 0 else b"")
            self.data_end = max(self.data_end, start + max(size, 0))
        while len(self.names) < len(out):
            self.names.append(f"scene{len(self.names):04d}")
        return out

    def _unmask(self, chunk: bytes) -> bytes:
        return _xor(_xor(chunk, self.key or b""), SCENE_KEY)

    def _detect_compressed(self) -> bool:
        for c in self.chunks:
            if c:
                if _lzss_header_ok(self._unmask(c)):
                    return True
                if self.exe_angou and self.key is not None:
                    raise SiglusError("wrong game key (scene data does not decode)")
                return False
        return False

    def _decode(self, chunk: bytes) -> bytes:
        if not chunk:
            return b""
        if not self.compressed:
            return _xor(chunk, self.key or b"")
        return lzss_unpack(self._unmask(chunk))

    def _encode(self, ss: bytes) -> bytes:
        if not self.compressed:
            return _xor(ss, self.key or b"")
        return _xor(_xor(lzss_pack(ss), SCENE_KEY), self.key or b"")

    def _detect_codec(self) -> bool:
        sample = [s for s in self.scenes if s.data][:20]
        xor_score = sum(_string_score(read_strings(s.data, True)) for s in sample)
        plain_score = sum(_string_score(read_strings(s.data, False)) for s in sample)
        return xor_score >= plain_score

    def rebuild(self, new_strings: dict[int, list[str]]) -> bytes:
        """New Scene.pck bytes with the given scenes' strings replaced."""
        chunks = []
        for i, (scene, chunk) in enumerate(zip(self.scenes, self.chunks)):
            if i in new_strings and scene.data:
                chunks.append(self._encode(write_strings(scene.data, new_strings[i], self.string_xor)))
            else:
                chunks.append(chunk)
        h = self.header
        out = bytearray(self.raw[: h[19]])
        offset = 0
        for i, c in enumerate(chunks):
            struct.pack_into("<ii", out, h[17] + i * 8, offset if c else 0, len(c))
            offset += len(c)
        for c in chunks:
            out += c
        out += self.raw[self.data_end :]  # optional original-source archive
        return bytes(out)


def parse_key(text: str) -> bytes | None:
    """16 bytes from text such as '36 0F C9 6E ...' or '0x36, 0x0f, ...'."""
    import re

    hexes = re.findall(r"(?:0x)?([0-9A-Fa-f]{2})(?![0-9A-Fa-f])", text)
    if len(hexes) < 16:
        return None
    return bytes(int(h, 16) for h in hexes[:16])
