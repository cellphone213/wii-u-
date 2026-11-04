"""Create a small fake Ren'Py game (compiled scripts only) for smoke tests.

Usage: python tools/make_demo_game.py <folder>
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
from fake_renpy import rpyc_bytes, sample_script  # noqa: E402

root = Path(sys.argv[1])
(root / "game").mkdir(parents=True, exist_ok=True)
(root / "renpy").mkdir(exist_ok=True)
(root / "renpy" / "vc_version.py").write_text("version = '8.3.7.25031702'\n")
(root / "game" / "script.rpyc").write_bytes(rpyc_bytes(sample_script("cslots")))
print("demo game at", root)
