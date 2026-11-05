"""Synthetic Anim / Crowd (SIS Planning) game: zz_sce.dat + zz_define.dat."""

from __future__ import annotations

import struct
from pathlib import Path

from vnarabizer.archives.anim import encrypt

HEADER = b"\x00\x00\x00\x01" + bytes.fromhex("5a3c9e01f27b44d08812c6e3ab907d15")
STRINGS = [
    "BGB", "w000001d", "みやび", "「鬼灯連嗣＜ほおずき　れんじ＞君。ようこそ」",
    "『迎えに来たんだ@n　お嫁さんになって下さい』", "？？？", "Mmiyabi030", "７月１日。時刻は朝８時を過ぎた頃。",
]
DEFINE_TEXTS = ["回想１：ハッピーバースデー", "あをい「こんにちは」@n連嗣「やあ」"]


def sce_body(strings=STRINGS) -> bytes:
    # bytecode: opcode, string index (instructions refer to strings by index)
    code = b"".join(struct.pack("<ii", 0x14, i) for i in range(len(strings)))
    labels = b"\x01"
    table = b"\0".join(s.encode("cp932") for s in strings) + b"\0\0\0"
    label_ofs = 12 + len(code)
    table_ofs = label_ofs + len(labels)
    return struct.pack("<III", label_ofs, table_ofs, 0) + code + labels + table


def define_body() -> bytes:
    out = bytearray(struct.pack("<i", 0x13) + b"product\0")
    for i, t in enumerate(DEFINE_TEXTS):
        out += struct.pack("<ii", 2, i) + t.encode("cp932") + b"\0"
    out += struct.pack("<iiii", 4, 60, 573, 90)
    return bytes(out)


def make_anim_game(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "zz_sce.dat").write_bytes(encrypt(sce_body(), HEADER))
    (root / "zz_define.dat").write_bytes(encrypt(define_body(), HEADER))
    return root
