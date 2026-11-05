"""Create small fake games for smoke tests.

Usage: python tools/make_demo_game.py <folder> [renpy|mv|mz|sugarcube|harlowe|cs2|krkr]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

root = Path(sys.argv[1])
kind = sys.argv[2] if len(sys.argv) > 2 else "renpy"

if kind == "renpy":
    from fake_renpy import rpyc_bytes, sample_script

    (root / "game").mkdir(parents=True, exist_ok=True)
    (root / "renpy").mkdir(exist_ok=True)
    (root / "renpy" / "vc_version.py").write_text("version = '8.3.7.25031702'\n")
    (root / "game" / "script.rpyc").write_bytes(rpyc_bytes(sample_script("cslots")))
elif kind in ("mv", "mz"):
    from fake_rpgm import make_game

    make_game(root, mz=(kind == "mz"))
elif kind == "cs2":
    from fake_vn import make_catsystem2_game

    make_catsystem2_game(root)
elif kind == "krkr":
    from fake_vn import make_kirikiri_game

    make_kirikiri_game(root)
elif kind == "vxace":
    from fake_rgss import make_vxace_game

    make_vxace_game(root)
elif kind == "anim":
    from fake_anim import make_anim_game

    make_anim_game(root)
elif kind == "nscripter":
    from fake_jp import make_nscripter_game

    make_nscripter_game(root)
elif kind == "siglus":
    from fake_jp import make_siglus_game

    make_siglus_game(root, encrypted=False)
else:
    from fake_twine import make_twine

    make_twine(root, "Harlowe" if kind == "harlowe" else "SugarCube")
print(f"{kind} demo game at {root}")
