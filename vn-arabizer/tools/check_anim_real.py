"""Integration check on real Anim / Crowd script files (decrypted *_sce.dat / *_define.dat).

    python tools/check_anim_real.py zet_sce.dat zet_define.dat

The decrypted files are encrypted into a temporary game folder, translated
with a stand-in Arabic text, and checked: the bytecode, header, number of
strings and every non-text string must be unchanged; define files must keep
every code byte in place; removing the patch must restore the originals.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vnarabizer.archives import anim  # noqa: E402
from vnarabizer.engines import detect  # noqa: E402
from vnarabizer.engines.anim import AnimEngine, source_text  # noqa: E402

HEADER = b"\x00\x00\x00\x01" + bytes(range(0x30, 0x40))


def main(paths: list[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "game"
        root.mkdir()
        originals = {}
        for p in map(Path, paths):
            (root / p.name).write_bytes(anim.encrypt(p.read_bytes(), HEADER))
            originals[p.name] = (root / p.name).read_bytes()
        eng = detect(root)
        assert isinstance(eng, AnimEngine), eng
        entries = eng.extract()
        print(f"{eng.describe()}: {len(entries)} unique strings")
        assert len(entries) > 100
        eng.apply({("line", e.original): "مرحبا يا صديقي @n في هذا العالم" for e in entries})
        for name, raw in originals.items():
            new = (root / name).read_bytes()
            if name.lower().endswith("_sce.dat"):
                a, b = anim.SceScript.load(raw), anim.SceScript.load(new)
                assert a.header == b.header and a.code == b.code and len(a.strings) == len(b.strings)
                assert all(x == y for x, y in zip(a.strings, b.strings) if source_text(x) is None)
                n = sum(x != y for x, y in zip(a.strings, b.strings))
                print(f"  {name}: {len(b.strings)} strings, {n} translated, code and resource names unchanged")
            else:
                d0 = anim.DefineFile.load(raw)
                body = anim.decrypt(new)
                pos = 0
                for piece in d0.pieces:
                    if isinstance(piece, bytes):
                        assert body[pos : pos + len(piece)] == piece, pos
                        pos += len(piece)
                    else:
                        pos = body.index(b"\0", pos) + 1
                assert pos == len(body)
                print(f"  {name}: {len(d0.texts())} strings translated, code bytes unchanged")
        eng.remove()
        assert all((root / n).read_bytes() == raw for n, raw in originals.items())
        print("  removed: originals restored")
    print("Anim/Crowd real-file check OK")


if __name__ == "__main__":
    main(sys.argv[1:])
