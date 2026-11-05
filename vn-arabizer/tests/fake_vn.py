"""Real-format CatSystem2 and KiriKiri game files for tests.

The .cst layout follows VNTextPatch's CatSystemScript; the KIF encryption
is the inverse of GARbro's ArcINT decryption.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

from vnarabizer.archives.kif import _ALPHABET, LEBlowfish, MersenneTwister, passphrase_key, write_kif
from vnarabizer.archives.xp3 import write_xp3

CST_STRINGS = [
    (0x21, "ハロルド"),
    (0x20, "「こんにちは、世界！」"),
    (0x03, ""),
    (0x20, "今日は\\nいい天気ですね。"),
    (0x03, ""),
    (0x30, "1 bg 01"),
]


def make_cst(strings=CST_STRINGS) -> bytes:
    table = bytearray()
    addrs = []
    for typ, text in strings:
        addrs.append(len(table))
        table += bytes([0x01, typ]) + text.encode("cp932") + b"\x00"
    addr_table = b"".join(struct.pack("<i", a) for a in addrs)
    header_len = 0x10
    strings_offset = len(addr_table)  # relative to 0x10
    body = addr_table + bytes(table)
    script = struct.pack("<iiii", len(body), 0, 0, strings_offset) + body
    comp = zlib.compress(script)
    return b"CatScene" + struct.pack("<ii", len(comp), len(script)) + comp


def read_cst_strings(data: bytes) -> list[tuple[int, str]]:
    comp, size = struct.unpack_from("<ii", data, 8)
    script = zlib.decompress(data[16:]) if comp else data[16:]
    addr_off = 0x10 + struct.unpack_from("<i", script, 8)[0]
    str_off = 0x10 + struct.unpack_from("<i", script, 12)[0]
    out = []
    for i in range((str_off - addr_off) // 4):
        a = struct.unpack_from("<i", script, addr_off + 4 * i)[0]
        p = str_off + a
        end = script.index(b"\x00", p + 2)
        out.append((script[p + 1], script[p + 2 : end].decode("cp932", "replace")))
    return out


def _encipher_name(name: str, key: int) -> bytes:
    raw = bytearray(name.encode("cp932"))
    k = ((key >> 24) + (key >> 16) + (key >> 8) + key) & 0xFF
    for i in range(len(raw)):
        idx = _ALPHABET.find(chr(raw[i]))
        if idx != -1:
            j = 0x33 - idx
            j = (j + k % 0x34) % 0x34
            raw[i] = ord(_ALPHABET[j])
        k += 1
    return bytes(raw).ljust(0x40, b"\x00")


def write_encrypted_kif(path: Path, files: list[tuple[str, bytes]], passphrase: str, seed: int = 0x12345678) -> None:
    main_key = passphrase_key(passphrase)
    twister = MersenneTwister(seed)
    bf = LEBlowfish(struct.pack("<I", twister.rand()))
    count = len(files) + 1
    offset = 8 + count * 0x48
    table = bytearray(b"__key__.dat".ljust(0x40, b"\x00") + struct.pack("<II", 0, seed))
    blobs = []
    for i, (name, data) in enumerate(files, 1):
        enc_off, enc_size = struct.unpack("<II", bf.encrypt(struct.pack("<II", offset, len(data))))
        twister.srand((main_key + i) & 0xFFFFFFFF)
        table += _encipher_name(name, twister.rand()) + struct.pack("<II", (enc_off - i) & 0xFFFFFFFF, enc_size)
        blobs.append(bf.encrypt(data))
        offset += len(data)
    with open(path, "wb") as f:
        f.write(b"KIF\x00" + struct.pack("<i", count) + table)
        for b in blobs:
            f.write(b)


def make_catsystem2_game(root: Path, encrypted_passphrase: str | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    files = [("op01.cst", make_cst()), ("op02.cst", make_cst([(0x20, "さようなら。"), (0x03, "")])), ("startup.xml", b"<xml/>")]
    if encrypted_passphrase:
        write_encrypted_kif(root / "scene.int", files, encrypted_passphrase)
    else:
        write_kif(root / "scene.int", files)
    write_kif(root / "image.int", [("bg01.hg3", b"HG-3 fake")])
    return root


KS_TEXT = (
    "*start|はじまり\r\n"
    "[cm]\r\n"
    "【ハロルド】\r\n"
    "「こんにちは、世界！」[p]\r\n"
    "今日は[r]\r\n"
    "いい天気ですね。[p]\r\n"
    "[jump storage=\"next.ks\"]\r\n"
)


def make_kirikiri_game(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    ks = b"\xff\xfe" + KS_TEXT.encode("utf-16-le")
    write_xp3(root / "data.xp3", [("scenario/first.ks", ks), ("system/Config.tjs", b"// config"), ("image/bg.png", b"\x89PNG")])
    return root
