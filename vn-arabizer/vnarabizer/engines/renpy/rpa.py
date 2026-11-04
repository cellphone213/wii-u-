"""Read Ren'Py .rpa archives (versions 1, 2, 3 and the common 3.2 variant).

Archives are only ever read. The translation patch is installed as loose
files next to them, which Ren'Py prefers over archived copies, so the
original archives are never modified.
"""

from __future__ import annotations

from pathlib import Path

from .safepickle import decompress, loads


class RPAError(Exception):
    pass


class RPAArchive:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.index = self._read_index()

    def _read_index(self) -> dict[str, list[tuple]]:
        with open(self.path, "rb") as f:
            header = f.read(64)
            try:
                if header.startswith(b"RPA-3.0 "):
                    offset = int(header[8:24], 16)
                    key = int(header[25:33], 16)
                elif header.startswith(b"RPA-3.2 "):
                    # 3.2 inserts an extra field before the key.
                    parts = header.split(b"\n")[0].split()
                    offset = int(parts[1], 16)
                    key = int(parts[3], 16)
                elif header.startswith(b"RPA-2.0 "):
                    offset = int(header[8:24], 16)
                    key = 0
                else:
                    rpi = self.path.with_suffix(".rpi")
                    if rpi.exists():  # version 1: separate index file
                        return _normalise(loads(decompress(rpi.read_bytes())), 0)
                    raise RPAError(f"unknown archive header {header[:8]!r}")
            except (ValueError, IndexError) as e:
                raise RPAError(f"bad archive header: {e}") from e
            f.seek(offset)
            raw = loads(decompress(f.read()))
        return _normalise(raw, key)

    def names(self) -> list[str]:
        return sorted(self.index)

    def read(self, name: str) -> bytes:
        parts = self.index[name]
        out = bytearray()
        with open(self.path, "rb") as f:
            for part in parts:
                offset, length = part[0], part[1]
                start = part[2] if len(part) > 2 else b""
                # Same as Ren'Py's loader: the prefix bytes, then `length` bytes.
                f.seek(offset)
                out += start
                out += f.read(length)
        return bytes(out)


def _normalise(index: dict, key: int) -> dict[str, list[tuple]]:
    out: dict[str, list[tuple]] = {}
    for name, parts in index.items():
        if isinstance(name, bytes):
            name = name.decode("utf-8", "replace")
        fixed = []
        for p in parts:
            offset, length = p[0] ^ key, p[1] ^ key
            start = p[2] if len(p) > 2 else b""
            if isinstance(start, str):
                start = start.encode("latin-1", "replace")
            fixed.append((offset, length, start))
        out[str(name).replace("\\", "/")] = fixed
    return out


def find_archives(game_dir: Path) -> list[RPAArchive]:
    out = []
    for p in sorted(Path(game_dir).rglob("*.rpa")):
        try:
            out.append(RPAArchive(p))
        except Exception:
            continue
    return out
