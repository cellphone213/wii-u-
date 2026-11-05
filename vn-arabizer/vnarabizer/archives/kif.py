"""CatSystem2 KIF archives (.int), including Frontwing encryption.

Ported from GARbro's ArcINT.cs (MIT, morkt). Encrypted archives start with
an entry named ``__key__.dat``; entry names and table values are encrypted
with a key derived from a passphrase stored (Blowfish-encrypted) in the
game executable's resources ``DATA/V_CODE2`` and ``KEY/KEY_CODE``.

The engine also reads unencrypted archives, so patched archives are always
written unencrypted.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from Crypto.Cipher import Blowfish

MAGIC = b"KIF\x00"
_ALPHABET = "zyxwvutsrqponmlkjihgfedcbaZYXWVUTSRQPONMLKJIHGFEDCBA"


class KifError(Exception):
    pass


# -- helpers ---------------------------------------------------------------------
def _crc32_normal_table() -> list[int]:
    table = []
    for i in range(256):
        c = i << 24
        for _ in range(8):
            c = ((c << 1) ^ 0x04C11DB7) if c & 0x80000000 else (c << 1)
            c &= 0xFFFFFFFF
        table.append(c)
    return table


_CRC = _crc32_normal_table()


def passphrase_key(password: str) -> int:
    """KeyData.EncodePassPhrase from GARbro."""
    key = 0xFFFFFFFF
    for c in password.encode("cp932"):
        key = (~_CRC[(key >> 24) ^ c] ^ (key << 8)) & 0xFFFFFFFF
    return key


class MersenneTwister:
    """MT19937 with the old Knuth-style seeding used by CatSystem2."""

    def __init__(self, seed: int):
        self.srand(seed)

    def srand(self, seed: int) -> None:
        mt = []
        seed &= 0xFFFFFFFF
        for _ in range(624):
            upper = seed & 0xFFFF0000
            seed = (69069 * seed + 1) & 0xFFFFFFFF
            mt.append(upper | ((seed & 0xFFFF0000) >> 16))
            seed = (69069 * seed + 1) & 0xFFFFFFFF
        self.mt = mt
        self.mti = 624

    def rand(self) -> int:
        mt = self.mt
        if self.mti >= 624:
            for kk in range(624):
                y = (mt[kk] & 0x80000000) | (mt[(kk + 1) % 624] & 0x7FFFFFFF)
                mt[kk] = mt[(kk + 397) % 624] ^ (y >> 1) ^ (0x9908B0DF if y & 1 else 0)
            self.mti = 0
        y = mt[self.mti]
        self.mti += 1
        y ^= y >> 11
        y ^= (y << 7) & 0x9D2C5680
        y ^= (y << 15) & 0xEFC60000
        y ^= y >> 18
        return y & 0xFFFFFFFF


def _swap_halves(data: bytes) -> bytes:
    """GARbro treats each 8-byte block as two little-endian words; PyCryptodome uses big-endian."""
    out = bytearray(data)
    for i in range(0, len(out) - len(out) % 4, 4):
        out[i : i + 4] = out[i : i + 4][::-1]
    return bytes(out)


class LEBlowfish:
    def __init__(self, key: bytes):
        self._bf = Blowfish.new(key, Blowfish.MODE_ECB)

    def decrypt(self, data: bytes) -> bytes:
        n = len(data) // 8 * 8
        if not n:
            return data
        body = _swap_halves(self._bf.decrypt(_swap_halves(data[:n])))
        return body + data[n:]

    def encrypt(self, data: bytes) -> bytes:
        n = len(data) // 8 * 8
        body = _swap_halves(self._bf.encrypt(_swap_halves(data[:n])))
        return body + data[n:]


def _decipher_name(raw: bytes, key: int) -> str:
    name = bytearray(raw.split(b"\x00", 1)[0])
    k = ((key >> 24) + (key >> 16) + (key >> 8) + key) & 0xFF
    for i in range(len(name)):
        j = _ALPHABET.find(chr(name[i]))
        if j != -1:
            j -= k % 0x34
            if j < 0:
                j += 0x34
            name[i] = ord(_ALPHABET[0x33 - j])
        k += 1
    return name.decode("cp932", "replace")


# -- passphrase from the game executable --------------------------------------------
def passphrase_from_exe(exe: Path) -> str | None:
    """Read the archive passphrase from a CatSystem2 executable, if it has one."""
    import pefile

    try:
        pe = pefile.PE(str(exe), fast_load=True)
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
    except Exception:
        return None
    res = {}
    for t in getattr(pe, "DIRECTORY_ENTRY_RESOURCE", None).entries if hasattr(pe, "DIRECTORY_ENTRY_RESOURCE") else []:
        tname = str(t.name) if t.name else None
        for n in getattr(t, "directory", None).entries if hasattr(t, "directory") else []:
            nname = str(n.name) if n.name else None
            for lang in n.directory.entries:
                d = lang.data.struct
                res[(tname, nname)] = pe.get_data(d.OffsetToData, d.Size)
                break
    code = res.get(("DATA", "V_CODE2"))
    if not code or len(code) < 8:
        return None
    key = res.get(("KEY", "KEY_CODE"))
    key = bytes(b ^ 0xCD for b in key) if key else b"windmill"
    plain = LEBlowfish(key).decrypt(code)
    plain = plain.split(b"\x00", 1)[0]
    return plain.decode("cp932", "replace")


# -- archive -------------------------------------------------------------------------
@dataclass
class KifEntry:
    name: str
    offset: int
    size: int


class KifArchive:
    def __init__(self, path: Path, passphrase: str | None = None):
        self.path = Path(path)
        self.encrypted = False
        self._blowfish: LEBlowfish | None = None
        self.entries = self._read_index(passphrase)

    def _read_index(self, passphrase: str | None) -> list[KifEntry]:
        with open(self.path, "rb") as f:
            head = f.read(8)
            if head[:4] != MAGIC:
                raise KifError("not a KIF archive")
            count = struct.unpack("<i", head[4:8])[0]
            if count <= 0 or count > 1_000_000:
                raise KifError("bad entry count")
            table = f.read(count * 0x48)
        first = table[:0x40].split(b"\x00", 1)[0]
        if first == b"__key__.dat":
            if passphrase is None:
                raise KifError("encrypted archive: game passphrase needed")
            self.encrypted = True
            main_key = passphrase_key(passphrase)
            seed = struct.unpack_from("<I", table, 0x44)[0]
            twister = MersenneTwister(seed)
            bf_key = struct.pack("<I", twister.rand())
            self._blowfish = LEBlowfish(bf_key)
            out = []
            for i in range(1, count):
                base = i * 0x48
                raw_name = table[base : base + 0x40]
                off, size = struct.unpack_from("<II", table, base + 0x40)
                off = (off + i) & 0xFFFFFFFF
                dec = self._blowfish.decrypt(struct.pack("<II", off, size))
                off, size = struct.unpack("<II", dec)
                twister.srand((main_key + i) & 0xFFFFFFFF)
                name = _decipher_name(raw_name, twister.rand())
                out.append(KifEntry(name, off, size))
            return out
        out = []
        for i in range(count):
            base = i * 0x48
            name = table[base : base + 0x40].split(b"\x00", 1)[0].decode("cp932", "replace")
            off, size = struct.unpack_from("<II", table, base + 0x40)
            out.append(KifEntry(name, off, size))
        return out

    def names(self) -> list[str]:
        return [e.name for e in self.entries]

    def read(self, name_or_entry) -> bytes:
        e = name_or_entry if isinstance(name_or_entry, KifEntry) else next(x for x in self.entries if x.name.lower() == name_or_entry.lower())
        with open(self.path, "rb") as f:
            f.seek(e.offset)
            data = f.read(e.size)
        if self._blowfish:
            data = self._blowfish.decrypt(data)
        return data


def write_kif(path: Path, files: list[tuple[str, bytes]]) -> None:
    """Write an unencrypted KIF archive (names up to 64 bytes in cp932)."""
    header = MAGIC + struct.pack("<i", len(files))
    table_size = len(files) * 0x48
    offset = len(header) + table_size
    table = bytearray()
    for name, data in files:
        raw = name.encode("cp932")
        if len(raw) > 0x40:
            raise KifError(f"file name too long for KIF: {name}")
        table += raw.ljust(0x40, b"\x00") + struct.pack("<II", offset, len(data))
        offset += len(data)
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "wb") as f:
        f.write(header)
        f.write(table)
        for _, data in files:
            f.write(data)
    tmp.replace(path)
