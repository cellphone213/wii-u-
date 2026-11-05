"""Print the text of a patched CatSystem2 scene.int, KiriKiri patch.xp3 or Anim/Crowd *_sce.dat (CI smoke test).

Usage: python tools/show_patched.py <scene.int | patch.xp3 | *_sce.dat>
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

path = Path(sys.argv[1])
arabic = re.compile(r"[؀-ۿﭐ-﷿ﹰ-﻿]")
found = False
if path.suffix.lower() == ".int":
    from fake_vn import read_cst_strings
    from vnarabizer.archives.kif import KifArchive

    arc = KifArchive(path)
    for e in arc.entries:
        if e.name.endswith(".cst"):
            for typ, text in read_cst_strings(arc.read(e)):
                # CatSystem2 stores characters outside Shift-JIS as \$code; escapes.
                decoded = re.sub(r"\\\$(\d+);", lambda m: chr(int(m.group(1))), text)
                print(f"{e.name} [{typ:02x}] {decoded}")
                found = found or bool(arabic.search(decoded))
elif path.name.lower().endswith("_sce.dat"):
    from vnarabizer.archives.anim import SceScript
    from vnarabizer.core.sjis_tunnel import SjisTunnel
    from vnarabizer.engines.anim import AnimEngine

    # The Shift-JIS tunnel table is next to the game exe, or in the work folder when there is no exe.
    table = path.parent / "sjis_ext.bin"
    if not table.exists():
        table = AnimEngine(path.parent).work / "sjis_ext.bin"
    tunnel = SjisTunnel(table.read_bytes())
    for i, s in enumerate(SceScript.load(path.read_bytes()).strings):
        if s:
            text = tunnel.decode(s)
            print(f"#{i} {text}")
            found = found or bool(arabic.search(text))
else:
    from vnarabizer.archives.xp3 import Xp3Archive

    arc = Xp3Archive(path)
    for e in arc.entries:
        data = arc.read(e)
        text = data[2:].decode("utf-16-le") if data[:2] == b"\xff\xfe" else data.decode("utf-8", "replace")
        print(f"--- {e.name}\n{text}")
        found = found or bool(arabic.search(text))
if not found:
    print("ERROR: no Arabic found in the patched scripts")
    sys.exit(1)
