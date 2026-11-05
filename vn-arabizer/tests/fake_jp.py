"""Synthetic NScripter and Siglus games for tests."""

from __future__ import annotations

import struct
from pathlib import Path

from vnarabizer.archives.siglus import PACK_FIELDS, SCENE_KEY, SCN_FIELDS, _str_key, _xor, lzss_pack

NSCRIPT = (
    "*define\r\n"
    "caption \"テストゲーム\"\r\n"
    "game\r\n"
    "*start\r\n"
    "bg \"bg\\room.jpg\",1\r\n"
    "【ハロルド】「こんにちは、世界！」@\r\n"
    "今日はいい天気ですね。\\\r\n"
    "select \"はい\",*yes,\r\n"
    "       \"いいえ\",*no\r\n"
    "*yes\r\n"
    "#FF0000よかった！@\r\n"
    "end\r\n"
)


def make_nscripter_game(root: Path, plain_txt: bool = False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    data = NSCRIPT.encode("cp932")
    if plain_txt:
        (root / "0.txt").write_bytes(data)
    else:
        (root / "nscript.dat").write_bytes(bytes(b ^ 0x84 for b in data))
    (root / "arc.nsa").write_bytes(b"\x00\x00")
    return root


def make_ss(strings: list[str], xor: bool = True) -> bytes:
    code = b"\x01\x02\x03\x04"
    header_size = SCN_FIELDS * 4
    out = bytearray(header_size)
    h = [0] * SCN_FIELDS
    h[0] = header_size
    h[1], h[2] = len(out), len(code)
    out += code
    index = bytearray()
    body = bytearray()
    pos = 0
    for i, s in enumerate(strings):
        units = list(struct.unpack(f"<{len(s)}H", s.encode("utf-16-le")))
        if xor:
            units = [u ^ _str_key(i) for u in units]
        index += struct.pack("<ii", pos, len(units))
        body += struct.pack(f"<{len(units)}H", *units)
        pos += len(units)
    h[3], h[4] = len(out), len(strings)
    out += index
    h[5], h[6] = len(out), len(strings)
    out += body
    for k in range(7, SCN_FIELDS, 2):
        h[k] = len(out)
    struct.pack_into(f"<{SCN_FIELDS}i", out, 0, *h)
    return bytes(out)


def make_scene_pck(scenes: dict[str, list[str]], key: bytes | None = None) -> bytes:
    header_size = PACK_FIELDS * 4
    out = bytearray(header_size)
    h = [0] * PACK_FIELDS
    h[0] = header_size
    for k in range(1, 13, 2):
        h[k] = header_size
    names = list(scenes)
    h[13], h[14] = len(out), len(names)
    pos = 0
    for n in names:
        out += struct.pack("<ii", pos, len(n))
        pos += len(n)
    h[15], h[16] = len(out), len(names)
    for n in names:
        out += n.encode("utf-16-le")
    chunks = []
    for n in names:
        c = _xor(lzss_pack(make_ss(scenes[n])), SCENE_KEY)
        chunks.append(_xor(c, key) if key else c)
    h[17], h[18] = len(out), len(chunks)
    pos = 0
    for c in chunks:
        out += struct.pack("<ii", pos, len(c))
        pos += len(c)
    h[19], h[20] = len(out), len(chunks)
    h[21] = 1 if key else 0
    for c in chunks:
        out += c
    struct.pack_into(f"<{PACK_FIELDS}i", out, 0, *h)
    return bytes(out)


SIGLUS_SCENES = {"s01": ["【ハロルド】", "「こんにちは、世界！」", "bg01", "今日はいい天気ですね。"], "s02": ["さようなら。"]}
SIGLUS_KEY = bytes.fromhex("360FC96E2ED38B0F66A570C323A36E94")


def make_siglus_game(root: Path, encrypted: bool = True) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "Scene.pck").write_bytes(make_scene_pck(SIGLUS_SCENES, SIGLUS_KEY if encrypted else None))
    (root / "Gameexe.dat").write_bytes(b"\x00" * 16)
    return root
