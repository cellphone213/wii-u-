"""Builders for synthetic game archives (layouts as GARbro reads them)."""

from __future__ import annotations

import hashlib
import struct
import zlib
from pathlib import Path

from vnarabizer.archives.packs import SWAP_00, _rotr8, _swap_len, xor_cycle

LINES = ["「こんにちは、世界！」", "今日はいい天気ですね。"]
BURIKO = b"BurikoCompiledScriptVer1.00\0"


def script(header: bytes, lines=LINES) -> bytes:
    return header + b"\n" + b"\n".join(("JP:" + l).encode("utf-8") for l in lines)


# -- BGI ---------------------------------------------------------------------------------
def dsc_encode(data: bytes, key: int = 0x12345678) -> bytes:
    """DSC stream: literals at depth 8 (255 of them) / 9, and one back-reference code."""

    magic = struct.unpack("<H", b"DS")[0] << 16
    depths = [8] * 255 + [9] + [0] * 256
    backref = 256 + 3  # copies 5 bytes
    depths[backref] = 9
    state = [key]

    def update_key():
        k = state[0]
        v0 = (20021 * (k & 0xFFFF)) & 0xFFFFFFFF
        v1 = (magic | (k >> 16)) & 0xFFFFFFFF
        v1 = (v1 * 20021 + k * 346) & 0xFFFFFFFF
        v1 = (v1 + (v0 >> 16)) & 0xFFFF
        state[0] = ((v1 << 16) + (v0 & 0xFFFF) + 1) & 0xFFFFFFFF
        return v1 & 0xFF

    table = bytes((d + update_key()) & 0xFF for d in depths)
    # Learn each code's bit path from the decoder's own tree builder.
    paths = _paths(depths)
    bits = []
    count = 0
    i = 0
    while i < len(data):
        if i >= 7 and i + 5 <= len(data) and data[i : i + 5] == data[i - 7 : i - 2]:
            bits.append(paths[backref] + format(7 - 2, "012b"))
            i += 5
        else:
            bits.append(paths[data[i]])
            i += 1
        count += 1
    s = "".join(bits)
    s += "0" * (-len(s) % 8)
    payload = int(s, 2).to_bytes(len(s) // 8, "big") if s else b""
    head = b"DSC FORMAT 1.00\0" + struct.pack("<IiII", key, len(data), count, 0)
    return head + table + payload + b"\0" * 8


def _paths(depths):
    codes = sorted((d, i) for i, d in enumerate(depths) if d)
    nodes, index = {}, [[0] * 1024, [0] * 1024]
    next_node, depth_nodes, depth, child, n = 1, 1, 0, 0, 0
    while n < len(codes):
        cur, child = child, child ^ 1
        existed = 0
        while n < len(codes) and codes[n][0] == depth:
            nodes[index[cur][existed]] = ("leaf", codes[n][1])
            n += 1
            existed += 1
        create = depth_nodes - existed
        for i in range(create):
            l, r = next_node, next_node + 1
            next_node += 2
            index[child][i * 2], index[child][i * 2 + 1] = l, r
            nodes[index[cur][existed + i]] = ("node", l, r)
        depth += 1
        depth_nodes = create * 2
    out = {}

    def walk(node, path):
        kind = nodes[node]
        if kind[0] == "leaf":
            out[kind[1]] = path
        else:
            walk(kind[1], path + "0")
            walk(kind[2], path + "1")

    walk(0, "")
    return out


def make_bgi_arc(path: Path, files: list[tuple[str, bytes]], v2: bool = False) -> None:
    rec, nl = (0x80, 0x60) if v2 else (0x20, 0x10)
    out = bytearray((b"BURIKO ARC20" if v2 else b"PackFile    ") + struct.pack("<I", len(files)))
    pos = 0
    for name, data in files:
        out += name.encode("cp932").ljust(nl, b"\0") + struct.pack("<II", pos, len(data)) + b"\xAB" * (rec - nl - 8)
        pos += len(data)
    for _, data in files:
        out += data
    path.write_bytes(bytes(out))


# -- Majiro ------------------------------------------------------------------------------
def make_majiro_arc(path: Path, files: list[tuple[str, bytes]], version: int = 3) -> None:
    esize = 4 * (version + 1)
    hsize = 8 if version == 3 else 4
    rows = len(files) + (1 if version == 1 else 0)
    names = b"".join(n.encode("cp932") + b"\0" for n, _ in files)
    names_ofs = 0x1C + rows * esize
    data_ofs = names_ofs + len(names)
    out = bytearray(f"MajiroArcV{version}.000\0".encode() + struct.pack("<iII", len(files), names_ofs, data_ofs))
    pos = data_ofs
    for i, (n, d) in enumerate(files):
        out += (0x1000 + i).to_bytes(hsize, "little") + struct.pack("<I", pos)
        if version != 1:
            out += struct.pack("<I", len(d))
        pos += len(d)
    if version == 1:
        out += b"\0" * hsize + struct.pack("<I", pos)
    out += names
    for _, d in files:
        out += d
    path.write_bytes(bytes(out))


# -- AdvHD -------------------------------------------------------------------------------
def make_advhd_arc(path: Path, files: list[tuple[str, bytes]]) -> None:
    from vnarabizer.archives.packs import _ROL2

    index = bytearray()
    pos = 0
    stored = [d.translate(_ROL2) if n.endswith(".ws2") else d for n, d in files]
    for (n, _), d in zip(files, stored):
        index += struct.pack("<II", len(d), pos) + n.encode("utf-16-le") + b"\0\0"
        pos += len(d)
    path.write_bytes(struct.pack("<iI", len(files), len(index)) + bytes(index) + b"".join(stored))


# -- Artemis -----------------------------------------------------------------------------
def make_pfs(path: Path, files: list[tuple[str, bytes]], version: int = 8) -> None:
    index = bytearray(struct.pack("<I", len(files)))
    fields = []
    for n, d in files:
        b = n.replace("/", "\\").encode("utf-8")
        index += struct.pack("<I", len(b)) + b + b"\0\0\0\0"
        fields.append(len(index))
        index += b"\0" * 8
    index += struct.pack("<I", len(files) + 1) + b"".join(struct.pack("<Q", f) for f in fields) + b"\0" * 8
    pos = 7 + len(index)
    for f, (_, d) in zip(fields, files):
        struct.pack_into("<II", index, f, pos, len(d))
        pos += len(d)
    key = hashlib.sha1(bytes(index)).digest() if version == 8 else b""
    body = b"".join(xor_cycle(d, key) for _, d in files)
    path.write_bytes(b"pf" + str(version).encode() + struct.pack("<I", len(index)) + bytes(index) + body)


# -- YU-RIS ------------------------------------------------------------------------------
def make_ypf(path: Path, files: list[tuple[str, bytes]], version: int = 0x1F4, key: int = 0x40) -> None:
    extra = 4 if version >= 0x1D9 else 0
    recs = []
    for n, d in files:
        nb = bytes(b ^ key for b in n.replace("/", "\\").encode("cp932"))
        packed = n.endswith(".ybn")
        stored = zlib.compress(d) if packed else d
        recs.append((nb, packed, d, stored))
    index_len = sum(5 + len(nb) + 0x12 + extra for nb, *_ in recs)
    pos = 0x20 + index_len
    index = bytearray()
    for nb, packed, d, stored in recs:
        enc_len = None
        for v in range(256):
            if _swap_len(SWAP_00, v) == len(nb):
                enc_len = v
                break
        index += struct.pack("<I", zlib.crc32(nb)) + bytes([enc_len ^ 0xFF]) + nb
        index += bytes([0, 1 if packed else 0]) + struct.pack("<III", len(d), len(stored), pos)
        if extra:
            index += b"\0" * extra
        index += struct.pack("<I", zlib.adler32(stored))
        pos += len(stored)
    head = b"YPF\0" + struct.pack("<IiI", version, len(files), 0x20 + index_len).ljust(28, b"\0")
    path.write_bytes(head + bytes(index) + b"".join(s for *_, s in recs))


# -- Softpal -----------------------------------------------------------------------------
def softpal_encrypt(data: bytes) -> bytes:
    out = bytearray(data)
    shift = 4
    for p in range(16, 16 + (len(out) - 16) // 4 * 4, 4):
        v = struct.unpack_from("<I", out, p)[0] ^ (0x084DF873 ^ 0xFF987DEE)
        struct.pack_into("<I", out, p, v)
        out[p] = _rotr8(out[p], shift)
        shift += 1
    return bytes(out)


def make_softpal_pac(path: Path, files: list[tuple[str, bytes]]) -> None:
    head = bytearray(struct.pack("<I", len(files)).ljust(0x3FE, b"\0"))
    base = 0x3FE + len(files) * 0x28
    pos = base
    for n, d in files:
        head += n.encode("cp932").ljust(0x20, b"\0") + struct.pack("<II", len(d), pos)
        pos += len(d)
    path.write_bytes(bytes(head) + b"".join(d for _, d in files) + b"EOF \x00\x00\x00\x00")
