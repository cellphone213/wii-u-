"""Print the text of a patched CatSystem2 scene.int or KiriKiri patch.xp3 (CI smoke test).

Usage: python tools/show_patched.py <scene.int | patch.xp3>
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
