"""Game archives that hold Japanese VN scripts: read, and write back patched.

Readers follow GARbro (morkt, MIT licence): Ethornell/ArcBGI.cs,
Majiro/ArcMajiro.cs, Will/ArcPulltop.cs, Artemis/ArcPFS.cs, YuRis/ArcYPF.cs,
Softpal/ArcPAC.cs.

Writers keep the original index (names, hashes, order, padding) and only
rewrite each entry's offset and size, so anything we do not understand in
the index is carried over unchanged. Replaced entries are stored without
compression where the format allows it, which the engines accept.
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

_CHUNK = 4 * 1024 * 1024


class PackError(Exception):
    pass


@dataclass
class PEntry:
    name: str
    offset: int  # absolute
    size: int
    meta: dict = field(default_factory=dict)


def _u32(b: bytes, o: int) -> int:
    return struct.unpack_from("<I", b, o)[0]


def _rotl8(v: int, n: int) -> int:
    n &= 7
    return ((v << n) | (v >> (8 - n))) & 0xFF


def _rotr8(v: int, n: int) -> int:
    n &= 7
    return ((v >> n) | (v << (8 - n))) & 0xFF


_ROR2 = bytes(_rotr8(i, 2) for i in range(256))
_ROL2 = bytes(_rotl8(i, 2) for i in range(256))


def xor_cycle(data: bytes, key: bytes, start: int = 0) -> bytes:
    """data XOR key repeated, the key starting at position ``start`` of its cycle."""
    if not data or not any(key):
        return bytes(data)
    k = len(key)
    s = start % k
    stream = (key[s:] + key * (len(data) // k + 2))[: len(data)]
    return (int.from_bytes(data, "little") ^ int.from_bytes(stream, "little")).to_bytes(len(data), "little")


class Pack:
    """Base archive. Subclasses fill ``entries`` and implement ``_write_index``."""

    kind = ""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.size = self.path.stat().st_size
        self.entries: list[PEntry] = []
        with open(self.path, "rb") as f:
            self._parse(f)
        for e in self.entries:
            if e.offset < 0 or e.size < 0 or e.offset + e.size > self.size:
                raise PackError(f"{self.path.name}: entry {e.name} is outside the file")

    # -- reading ----------------------------------------------------------------------
    def _parse(self, f) -> None:
        raise NotImplementedError

    def raw(self, e: PEntry) -> bytes:
        with open(self.path, "rb") as f:
            f.seek(e.offset)
            return f.read(e.size)

    def peek(self, e: PEntry, n: int = 64) -> bytes:
        with open(self.path, "rb") as f:
            f.seek(e.offset)
            return f.read(min(n, e.size))

    def read(self, e: PEntry) -> bytes:
        """Entry contents as the game sees them (decrypted / decompressed)."""
        return self.raw(e)

    def encode(self, e: PEntry, data: bytes) -> bytes:
        """Bytes to store for new contents ``data`` of entry ``e``."""
        return data

    # -- writing ----------------------------------------------------------------------
    @property
    def data_start(self) -> int:
        return min((e.offset for e in self.entries if e.size), default=self.size)

    def _data_order(self) -> list[PEntry]:
        return [e for _, e in sorted(enumerate(self.entries), key=lambda t: (t[1].offset, t[0]))]

    def write(self, dest: Path, replacements: dict[str, bytes], progress: Callable[[float], None] = lambda f: None) -> None:
        """Write the archive to ``dest`` with entries (by name) replaced by new contents."""
        dest = Path(dest)
        if dest.resolve() == self.path.resolve():
            raise PackError("refusing to overwrite the archive being read")
        stored = {name: self.encode(self._by_name(name), data) for name, data in replacements.items()}
        start = self.data_start
        layout: dict[int, tuple[int, int]] = {}  # id(entry) -> (new offset, new size)
        pos = start
        order = self._data_order()
        for e in order:
            size = len(stored[e.name]) if e.name in stored else e.size
            layout[id(e)] = (pos, size)
            pos += size
        end_of_data = max((e.offset + e.size for e in self.entries), default=start)
        with open(self.path, "rb") as src:
            src.seek(0)
            head = bytearray(src.read(start))
            self._write_index(head, layout, stored)
            with open(dest, "wb") as out:
                out.write(head)
                total = max(1, sum(s for _, s in layout.values()))
                done = 0
                for e in order:
                    new_off, new_size = layout[id(e)]
                    for chunk in self._entry_chunks(src, e, stored.get(e.name), new_off):
                        out.write(chunk)
                    done += new_size
                    progress(done / total)
                if end_of_data < self.size:  # trailing data (footers etc.) kept as is
                    src.seek(end_of_data)
                    out.write(src.read())

    def _by_name(self, name: str) -> PEntry:
        for e in self.entries:
            if e.name == name:
                return e
        raise KeyError(name)

    def _entry_chunks(self, src, e: PEntry, new: bytes | None, new_off: int) -> Iterable[bytes]:
        if new is not None:
            yield new
            return
        src.seek(e.offset)
        left = e.size
        while left > 0:
            chunk = src.read(min(_CHUNK, left))
            if not chunk:
                raise PackError("unexpected end of archive")
            left -= len(chunk)
            yield chunk

    def _write_index(self, head: bytearray, layout: dict[int, tuple[int, int]], stored: dict[str, bytes]) -> None:
        raise NotImplementedError


# ======================================================================================
# BGI / Ethornell (.arc "PackFile    " and "BURIKO ARC20"), DSC compression
# ======================================================================================
class _MsbBits:
    def __init__(self, data: bytes, pos: int):
        self.data = data
        self.i = pos * 8
        self.end = len(data) * 8

    def bit(self) -> int:
        i = self.i
        if i >= self.end:
            raise PackError("DSC: end of data")
        self.i = i + 1
        return (self.data[i >> 3] >> (7 - (i & 7))) & 1

    def get(self, n: int) -> int:
        if self.i + n > self.end:
            return -1
        v = 0
        for _ in range(n):
            v = (v << 1) | self.bit()
        return v


def dsc_decode(data: bytes, limit: int | None = None) -> bytes:
    """Decompress a BGI "DSC FORMAT 1.00" blob (stops early after ``limit`` bytes)."""
    if not data.startswith(b"DSC FORMAT 1.00"):
        raise PackError("not DSC data")
    magic = struct.unpack_from("<H", data, 0)[0] << 16
    key, out_size, dec_count = struct.unpack_from("<IiI", data, 0x10)
    state = [key]

    def update_key() -> int:
        k = state[0]
        v0 = (20021 * (k & 0xFFFF)) & 0xFFFFFFFF
        v1 = (magic | (k >> 16)) & 0xFFFFFFFF
        v1 = (v1 * 20021 + k * 346) & 0xFFFFFFFF
        v1 = (v1 + (v0 >> 16)) & 0xFFFF
        state[0] = ((v1 << 16) + (v0 & 0xFFFF) + 1) & 0xFFFFFFFF
        return v1 & 0xFF

    codes = []
    for i in range(512):
        depth = (data[0x20 + i] - update_key()) & 0xFF
        if depth:
            codes.append((depth, i))
    codes.sort()
    # Tree as in GARbro's CreateHuffmanTree: node = (is_parent, code_or_left, right)
    nodes: dict[int, tuple[bool, int, int]] = {}
    index = [[0] * 1024, [0] * 1024]
    next_node, depth_nodes, depth, child, n = 1, 1, 0, 0, 0
    while n < len(codes):
        cur = child
        child ^= 1
        existed = 0
        while n < len(codes) and codes[n][0] == depth:
            nodes[index[cur][existed]] = (False, codes[n][1], 0)
            n += 1
            existed += 1
        create = depth_nodes - existed
        if create < 0 or next_node + 2 * create > 1023:
            raise PackError("DSC: bad Huffman table")
        for i in range(create):
            left, right = next_node, next_node + 1
            next_node += 2
            index[child][i * 2], index[child][i * 2 + 1] = left, right
            nodes[index[cur][existed + i]] = (True, left, right)
        depth += 1
        depth_nodes = create * 2
    out = bytearray()
    bits = _MsbBits(data, 0x220)
    target = out_size if limit is None else min(out_size, limit)
    for _ in range(dec_count):
        node = 0
        while True:
            is_parent, a, b = nodes.get(node, (False, -1, 0))
            if not is_parent:
                break
            node = b if bits.bit() else a
        code = nodes[node][1]
        if code < 0:
            raise PackError("DSC: broken stream")
        if code >= 256:
            offset = bits.get(12)
            if offset < 0:
                break
            count = (code & 0xFF) + 2
            offset += 2
            start = len(out) - offset
            if start < 0:
                raise PackError("DSC: bad back-reference")
            for i in range(count):
                out.append(out[start + i])
        else:
            out.append(code)
        if len(out) >= target:
            break
    return bytes(out[:target])


class BgiArc(Pack):
    kind = "BGI"

    def _parse(self, f) -> None:
        head = f.read(16)
        if head[:12] == b"PackFile    ":
            rec, name_len = 0x20, 0x10
        elif head[:12] == b"BURIKO ARC20":
            rec, name_len = 0x80, 0x60
        else:
            raise PackError("not a BGI archive")
        count = _u32(head, 12)
        if count > 0xFFFFF:
            raise PackError("bad BGI entry count")
        index = f.read(rec * count)
        self.rec, self.base = rec, 0x10 + rec * count
        for i in range(count):
            o = i * rec
            name = index[o : o + name_len].split(b"\0", 1)[0].decode("cp932", "replace")
            off, size = struct.unpack_from("<II", index, o + name_len)
            self.entries.append(PEntry(name, self.base + off, size, {"idx": i, "name_len": name_len}))

    @property
    def data_start(self) -> int:
        return self.base

    def read(self, e: PEntry) -> bytes:
        data = self.raw(e)
        if data.startswith(b"DSC FORMAT 1.00"):
            return dsc_decode(data)
        return data

    def is_script(self, e: PEntry) -> bool:
        if "." in e.name:
            return False
        head = self.peek(e, 0x230)
        if head.startswith(b"BurikoCompiledScriptVer1.00"):
            return True
        if head.startswith(b"DSC FORMAT 1.00") and len(head) >= 0x220:
            try:
                return dsc_decode(self.raw(e), 32).startswith(b"BurikoCompiledScriptVer1.00")
            except Exception:
                return False
        return False

    def _write_index(self, head, layout, stored):
        for e in self.entries:
            off, size = layout[id(e)]
            struct.pack_into("<II", head, 0x10 + e.meta["idx"] * self.rec + e.meta["name_len"], off - self.base, size)


# ======================================================================================
# Majiro (.arc "MajiroArcV1.000" / V2 / V3)
# ======================================================================================
class MajiroArc(Pack):
    kind = "Majiro"

    def _parse(self, f) -> None:
        head = f.read(0x1C)
        if head[:10] != b"MajiroArcV" or head[11:16] != b".000\0":
            raise PackError("not a Majiro archive")
        self.version = head[10] - 0x30
        if self.version not in (1, 2, 3):
            raise PackError("unknown Majiro archive version")
        count, names_ofs, data_ofs = struct.unpack_from("<iII", head, 16)
        self.data_ofs = data_ofs
        self.esize = 4 * (self.version + 1)
        self.hsize = 8 if self.version == 3 else 4
        rows = count + (1 if self.version == 1 else 0)
        table = f.read(rows * self.esize)
        f.seek(names_ofs)
        names = f.read(data_ofs - names_ofs).split(b"\0")
        for i in range(count):
            o = i * self.esize
            off = _u32(table, o + self.hsize)
            if self.version == 1:
                size = _u32(table, o + self.esize + self.hsize) - off
            else:
                size = _u32(table, o + self.hsize + 4)
            self.entries.append(PEntry(names[i].decode("cp932", "replace"), off, size, {"idx": i}))

    @property
    def data_start(self) -> int:
        return self.data_ofs

    def _data_order(self) -> list[PEntry]:
        return list(self.entries)  # V1 derives sizes from the next entry's offset

    def is_script(self, e: PEntry) -> bool:
        return e.name.lower().endswith(".mjo")

    def _write_index(self, head, layout, stored):
        end = self.data_ofs
        for e in self.entries:
            off, size = layout[id(e)]
            o = 0x1C + e.meta["idx"] * self.esize + self.hsize
            struct.pack_into("<I", head, o, off)
            if self.version != 1:
                struct.pack_into("<I", head, o + 4, size)
            end = max(end, off + size)
        if self.version == 1:
            struct.pack_into("<I", head, 0x1C + len(self.entries) * self.esize + self.hsize, end)


# ======================================================================================
# AdvHD / WillPlus (.arc: count, index size, {size, offset, UTF-16 name})
# ======================================================================================
class AdvHdArc(Pack):
    kind = "AdvHD"

    def _parse(self, f) -> None:
        count, index_size = struct.unpack("<iI", f.read(8))
        if not 0 < count < 0x40000 or index_size > self.size:
            raise PackError("not an AdvHD archive")
        self.base = 8 + index_size
        index = f.read(index_size)
        pos = 0
        for i in range(count):
            if pos + 8 > len(index):
                raise PackError("not an AdvHD archive")
            size, off = struct.unpack_from("<II", index, pos)
            field_pos = 8 + pos
            pos += 8
            end = pos
            while end + 1 < len(index) and index[end : end + 2] != b"\0\0":
                end += 2
            if end + 1 >= len(index) or end == pos:
                raise PackError("not an AdvHD archive")
            name = index[pos:end].decode("utf-16-le", "replace")
            pos = end + 2
            self.entries.append(PEntry(name, self.base + off, size, {"field": field_pos}))
        if pos != index_size:
            raise PackError("not an AdvHD archive")
        self.rotated = "model" not in self.path.name.lower()

    @property
    def data_start(self) -> int:
        return self.base

    def _scripty(self, e: PEntry) -> bool:
        return self.rotated and e.name.lower().endswith((".ws2", ".json"))

    def read(self, e: PEntry) -> bytes:
        data = self.raw(e)
        return data.translate(_ROR2) if self._scripty(e) else data

    def encode(self, e: PEntry, data: bytes) -> bytes:
        return data.translate(_ROL2) if self._scripty(e) else data

    def is_script(self, e: PEntry) -> bool:
        return e.name.lower().endswith(".ws2")

    def _write_index(self, head, layout, stored):
        for e in self.entries:
            off, size = layout[id(e)]
            struct.pack_into("<II", head, e.meta["field"], size, off - self.base)


# ======================================================================================
# Artemis (.pfs: pf2 / pf6 / pf8; pf8 XORs data with SHA-1 of the index)
# ======================================================================================
class PfsArc(Pack):
    kind = "Artemis"

    def _parse(self, f) -> None:
        head = f.read(7)
        if head[:2] != b"pf" or head[2:3] not in (b"2", b"6", b"8"):
            raise PackError("not an Artemis archive")
        self.version = head[2] - 0x30
        index_size = _u32(head, 3)
        self.index_size = index_size
        index = f.read(index_size)
        if len(index) != index_size:
            raise PackError("truncated Artemis index")
        self.key = hashlib.sha1(index).digest() if self.version == 8 else b""
        if self.version == 2:
            count, pos, skip = _u32(index, 4), 8, 0x10
        else:
            count, pos, skip = _u32(index, 0), 4, 8
        for _ in range(count):
            n = _u32(index, pos)
            raw_name = index[pos + 4 : pos + 4 + n]
            try:
                name = raw_name.decode("utf-8")
            except UnicodeDecodeError:
                name = raw_name.decode("cp932", "replace")
            pos += n + skip
            off, size = struct.unpack_from("<II", index, pos)
            self.entries.append(PEntry(name.replace("\\", "/"), off, size, {"field": 7 + pos}))
            pos += 8

    @property
    def data_start(self) -> int:
        return 7 + self.index_size

    def read(self, e: PEntry) -> bytes:
        return xor_cycle(self.raw(e), self.key)

    def is_script(self, e: PEntry) -> bool:
        return e.name.lower().endswith((".ast", ".asb"))

    def write(self, dest, replacements, progress=lambda f: None):
        self._pending = replacements
        super().write(dest, replacements, progress)

    def encode(self, e, data):
        return data  # encrypted later with the new key, see _entry_chunks

    def _write_index(self, head, layout, stored):
        for e in self.entries:
            off, size = layout[id(e)]
            struct.pack_into("<II", head, e.meta["field"], off, size)
        self.new_key = hashlib.sha1(bytes(head[7 : 7 + self.index_size])).digest() if self.version == 8 else b""

    def _entry_chunks(self, src, e, new, new_off):
        if new is not None:
            yield xor_cycle(new, self.new_key)
            return
        # Re-key: old XOR new applied in one pass (both cycles start at the entry start).
        both = bytes(a ^ b for a, b in zip(self.key, self.new_key)) if self.key else b""
        src.seek(e.offset)
        left, done = e.size, 0
        step = _CHUNK - _CHUNK % 20
        while left > 0:
            chunk = src.read(min(step, left))
            left -= len(chunk)
            yield xor_cycle(chunk, both, done) if both else chunk
            done += len(chunk)


# ======================================================================================
# YU-RIS (.ypf)
# ======================================================================================
SWAP_00 = bytes([0x03, 0x48, 0x06, 0x35, 0x0C, 0x10, 0x11, 0x19, 0x1C, 0x1E, 0x09, 0x0B, 0x0D, 0x13, 0x15, 0x1B, 0x20, 0x23, 0x26, 0x29, 0x2C, 0x2F, 0x2E, 0x32])
SWAP_04 = SWAP_00[4:]
SWAP_10 = SWAP_00[10:]


def _swap_len(table: bytes, v: int) -> int:
    pos = table.find(bytes([v]))
    if pos < 0:
        return v
    return table[pos - 1] if pos & 1 else table[pos + 1]


class YpfArc(Pack):
    kind = "YU-RIS"

    def _parse(self, f) -> None:
        head = f.read(0x20)
        if head[:4] != b"YPF\0":
            raise PackError("not a YPF archive")
        self.version, count, dir_size = struct.unpack_from("<IiI", head, 4)
        index = f.read(max(dir_size, 0) + 0x20)
        v = self.version
        if v < 0x100:
            tables = [SWAP_04, SWAP_00, SWAP_10]
        elif 0x12C <= v < 0x196:
            tables = [SWAP_10, SWAP_00, SWAP_04]
        else:
            tables = [SWAP_00, SWAP_10, SWAP_04]
        extras = [4 if v >= 0x1D9 else 8 if v == 0xDE else 0] + [4, 0, 8]
        last = None
        for extra in dict.fromkeys(extras):
            for table in tables:
                try:
                    self.entries = self._scan(index, count, table, extra)
                    self.extra = extra
                    return
                except PackError as e:
                    last = e
        raise last or PackError("cannot read YPF index")

    def _scan(self, index: bytes, count: int, table: bytes, extra: int) -> list[PEntry]:
        out = []
        pos = 0
        key = None
        tail = 0x12 + extra
        for _ in range(count):
            if pos + 5 > len(index):
                raise PackError("YPF index overrun")
            n = _swap_len(table, index[pos + 4] ^ 0xFF)
            raw = index[pos + 5 : pos + 5 + n]
            if n < 4 or len(raw) != n:
                raise PackError("bad YPF name")
            if key is None:
                key = raw[-4] ^ 0x2E
            name = bytes(b ^ key for b in raw)
            if b"." not in name:
                raise PackError("bad YPF name")
            fpos = pos + 5 + n
            if fpos + tail > len(index):
                raise PackError("YPF index overrun")
            packed = index[fpos + 1]
            unpacked, size, off = struct.unpack_from("<III", index, fpos + 2)
            if off + size > self.size or packed > 1:
                raise PackError("YPF entry out of range")
            try:
                text = name.decode("cp932")
            except UnicodeDecodeError as e:
                raise PackError("bad YPF name") from e
            out.append(PEntry(text.replace("\\", "/"), off, size, {"f": 0x20 + fpos, "packed": packed, "unpacked": unpacked}))
            pos = fpos + tail
        return out

    def read(self, e: PEntry) -> bytes:
        data = self.raw(e)
        return zlib.decompress(data) if e.meta["packed"] else data

    def encode(self, e: PEntry, data: bytes) -> bytes:
        return zlib.compress(data, 9) if e.meta["packed"] else data

    def is_script(self, e: PEntry) -> bool:
        return e.name.lower().endswith(".ybn")

    def _sum_pos(self, e: PEntry) -> int:
        return e.meta["f"] + 0x12 + self.extra - 4

    def _stored_sum(self, e: PEntry) -> int:
        with open(self.path, "rb") as f:
            f.seek(self._sum_pos(e))
            return struct.unpack("<I", f.read(4))[0]

    def _checksum_fn(self):
        """The checksum the archive keeps for stored data (Adler-32 or CRC-32), if one matches."""
        sample = [e for e in self.entries if 0 < e.size < 4 * 1024 * 1024][:3]
        for fn in (zlib.adler32, zlib.crc32):
            if sample and all(fn(self.raw(e)) & 0xFFFFFFFF == self._stored_sum(e) for e in sample):
                return fn
        return None

    def _write_index(self, head, layout, stored):
        fn = self._checksum_fn() if stored else None
        self.checksum_kept = bool(stored) and fn is None
        for e in self.entries:
            off, size = layout[id(e)]
            f = e.meta["f"]
            if e.name in stored:
                unpacked = len(zlib.decompress(stored[e.name])) if e.meta["packed"] else size
                struct.pack_into("<II", head, f + 2, unpacked, size)
                if fn is not None:
                    struct.pack_into("<I", head, self._sum_pos(e), fn(stored[e.name]) & 0xFFFFFFFF)
            struct.pack_into("<I", head, f + 10, off)


# ======================================================================================
# Softpal (.pac): SCRIPT.SRC, TEXT.DAT, POINT.DAT
# ======================================================================================
class SoftpalPac(Pack):
    kind = "Softpal"

    def _parse(self, f) -> None:
        head = f.read(0x810)
        if head[:4] == b"PAC ":
            count, index_ofs, lens = _u32(head, 8), 0x804, (0x20,)
        else:
            count, index_ofs, lens = _u32(head, 0), 0x3FE, (0x20, 0x10)
        if not 0 < count < 0x40000:
            raise PackError("not a Softpal archive")
        for nl in lens:
            f.seek(index_ofs)
            index = f.read(count * (nl + 8))
            if len(index) == count * (nl + 8) and _u32(index, nl + 4) == index_ofs + count * (nl + 8):
                break
        else:
            raise PackError("not a Softpal archive")
        self.base = index_ofs + count * (nl + 8)
        for i in range(count):
            o = i * (nl + 8)
            name = index[o : o + nl].split(b"\0", 1)[0].decode("cp932", "replace")
            size, off = struct.unpack_from("<II", index, o + nl)
            self.entries.append(PEntry(name, off, size, {"field": index_ofs + o + nl}))

    @property
    def data_start(self) -> int:
        return self.base

    def read(self, e: PEntry) -> bytes:
        data = bytearray(self.raw(e))
        if len(data) <= 16 or data[0] != 0x24 or e.name.lower().endswith((".pgd", ".ogg", ".wav", ".bmp", ".png")):
            return bytes(data)
        shift = 4
        for p in range(16, 16 + (len(data) - 16) // 4 * 4, 4):
            data[p] = _rotl8(data[p], shift)
            shift += 1
            v = struct.unpack_from("<I", data, p)[0] ^ (0x084DF873 ^ 0xFF987DEE)
            struct.pack_into("<I", data, p, v)
        return bytes(data)

    def is_script(self, e: PEntry) -> bool:
        return e.name.upper() in ("SCRIPT.SRC", "TEXT.DAT", "POINT.DAT")

    def _write_index(self, head, layout, stored):
        for e in self.entries:
            off, size = layout[id(e)]
            struct.pack_into("<II", head, e.meta["field"], size, off)


OPENERS: tuple[type[Pack], ...] = (BgiArc, MajiroArc, PfsArc, YpfArc, SoftpalPac, AdvHdArc)


def open_pack(path: Path, kinds: Iterable[type[Pack]] = OPENERS) -> Pack | None:
    for cls in kinds:
        try:
            return cls(path)
        except (PackError, struct.error, ValueError, OSError, IndexError, zlib.error):
            continue
    return None
