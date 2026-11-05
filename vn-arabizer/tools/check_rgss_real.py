"""Integration check on real RPG Maker XP / VX / VX Ace archives.

    python tools/check_rgss_real.py path/to/Game.rgss3a [...]

Each archive is copied into a temporary game folder, its text extracted,
a stand-in Arabic translation applied, every data file re-read and checked,
and the patch removed again (the archive must come back byte-identical).
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vnarabizer.archives.rgss import RgssArchive  # noqa: E402
from vnarabizer.archives.rmarshal import dumps, loads  # noqa: E402
from vnarabizer.engines import detect  # noqa: E402
from vnarabizer.engines.rgss import RGSSEngine, SCRIPT_NAME  # noqa: E402

PRES = re.compile("[ﹰ-﻿]")


def check(archive: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "game"
        root.mkdir()
        shutil.copy2(archive, root / archive.name)
        (root / "Game.exe").write_bytes(b"MZ")
        original = (root / archive.name).read_bytes()
        eng = detect(root)
        assert isinstance(eng, RGSSEngine), eng
        entries = eng.extract()
        kinds: dict[str, int] = {}
        for e in entries:
            kinds[e.channel] = kinds.get(e.channel, 0) + 1
        print(f"{archive.name}: {eng.describe()}: {len(entries)} strings {kinds}")
        assert len(entries) > 50

        def fake(text: str) -> str:
            toks = re.findall(r"%\d+\$[sd]|\\[A-Za-z]+(?:\[\d+\])?|\\.", text)
            return "مرحبا بك " + " ".join(toks) + " في هذا العالم"

        eng.apply({(e.channel, e.original): fake(e.original) for e in entries})
        assert not [w for w in eng.warnings if "Fonts folder" not in w], eng.warnings
        arc = RgssArchive(root / archive.name)
        before = RgssArchive(root / "vnarabizer_backup" / archive.name)
        assert [e.name for e in arc.entries] == [e.name for e in before.entries]
        changed = 0
        for e in arc.entries:
            data = arc.read(e)
            if e.name.lower().endswith(("data", "data2")):
                assert dumps(loads(data)) == data, e.name
            if data != before.read(e.name):
                changed += 1
        scripts = next(e for e in arc.entries if e.name.lower().split("\\")[-1].startswith("scripts."))
        names = [s.items[1].text for s in loads(arc.read(scripts)).items]
        assert names.count(SCRIPT_NAME) == 1, names
        assert names.index(SCRIPT_NAME) == names.index("Main") - 1
        actors = next(e for e in arc.entries if e.name.lower().split("\\")[-1].startswith("actors."))
        assert PRES.search(loads(arc.read(actors)).items[1].get("@name").text)
        vocab = [s for s in loads(arc.read(scripts)).items if s.items[1].text == "Vocab"]
        if vocab:
            code = zlib.decompress(vocab[0].items[2].data).decode("utf-8")
            assert PRES.search(code) and "module Vocab" in code
        print(f"  patched: {changed} files changed, runtime script before Main, data re-reads byte-exact")
        eng.remove()
        assert (root / archive.name).read_bytes() == original
        print("  removed: archive restored")


if __name__ == "__main__":
    for a in sys.argv[1:]:
        check(Path(a))
    print("RGSS real-archive check OK")
