"""Create small fake games for smoke tests.

Usage: python tools/make_demo_game.py <folder> [renpy|mv|mz]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

root = Path(sys.argv[1])
kind = sys.argv[2] if len(sys.argv) > 2 else "renpy"

if kind == "renpy":
    from fake_renpy import rpyc_bytes, sample_script

    (root / "game").mkdir(parents=True, exist_ok=True)
    (root / "renpy").mkdir(exist_ok=True)
    (root / "renpy" / "vc_version.py").write_text("version = '8.3.7.25031702'\n")
    (root / "game" / "script.rpyc").write_bytes(rpyc_bytes(sample_script("cslots")))
else:
    from fake_rpgm import make_game

    make_game(root, mz=(kind == "mz"))
print(f"{kind} demo game at {root}")
