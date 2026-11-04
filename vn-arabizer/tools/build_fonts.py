"""Build the bundled game font: Noto Sans Arabic + the Latin part of Noto Sans.

Games still show some Latin text (numbers, names left in English, UI words),
and Arabic-only fonts have no Latin letters, so the two are merged into one
file. The result keeps Arabic presentation forms, which older Ren'Py versions
need for letter joining.

Usage: python tools/build_fonts.py [noto_dir]
Both source fonts are SIL Open Font License 1.1; so is the output.
"""

from __future__ import annotations

import sys
from pathlib import Path

from fontTools import subset
from fontTools.merge import Merger
from fontTools.ttLib import TTFont

OUT = Path(__file__).resolve().parent.parent / "vnarabizer" / "assets" / "fonts"
FAMILY = "VN Arabic"

LATIN_RANGES = [
    (0x0020, 0x007E),  # Basic Latin
    (0x00A0, 0x017F),  # Latin-1 + Latin Extended-A
    (0x2010, 0x205E),  # General punctuation (dashes, quotes, ellipsis)
    (0x20AC, 0x20AC),  # Euro sign
    (0x2122, 0x2122),
    (0x2190, 0x2193),  # arrows
    (0x2605, 0x2606),  # stars
    (0x2665, 0x2665),  # heart
    (0x266A, 0x266B),  # music notes
]


def subset_latin(src: Path, dst: Path) -> None:
    opts = subset.Options()
    opts.layout_features = ["*"]
    opts.name_IDs = ["*"]
    opts.notdef_outline = True
    opts.glyph_names = False
    opts.hinting = False
    font = TTFont(str(src))
    sub = subset.Subsetter(opts)
    cps = [c for a, b in LATIN_RANGES for c in range(a, b + 1)]
    sub.populate(unicodes=cps)
    sub.subset(font)
    font.save(str(dst))


def strip_for_merge(path: Path) -> None:
    """Drop tables the merger cannot combine (they only affect hinting/variations)."""
    f = TTFont(str(path))
    for tag in ("fpgm", "prep", "cvt ", "gasp", "hdmx", "LTSH", "VDMX", "DSIG", "STAT", "MVAR", "HVAR", "fvar", "gvar", "avar"):
        if tag in f:
            del f[tag]
    f.save(str(path))


def rename(font: TTFont, style: str) -> None:
    name = font["name"]
    full = f"{FAMILY} {style}"
    ps = f"VNArabic-{style}"
    for rec in list(name.names):
        if rec.nameID in (1, 16):
            rec.string = FAMILY
        elif rec.nameID in (2, 17):
            rec.string = style
        elif rec.nameID == 3:
            rec.string = f"VNArabizer:{ps}"
        elif rec.nameID == 4:
            rec.string = full
        elif rec.nameID == 6:
            rec.string = ps


def build(noto: Path, style: str) -> Path:
    tmp = OUT / "_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    arabic = tmp / f"ar-{style}.ttf"
    latin = tmp / f"la-{style}.ttf"
    arabic.write_bytes((noto / f"NotoSansArabic-{style}.ttf").read_bytes())
    subset_latin(noto / f"NotoSans-{style}.ttf", latin)
    strip_for_merge(arabic)
    strip_for_merge(latin)
    merged = Merger().merge([str(arabic), str(latin)])
    rename(merged, style)
    out = OUT / f"VNArabic-{style}.ttf"
    merged.save(str(out))
    for p in tmp.iterdir():
        p.unlink()
    tmp.rmdir()
    return out


def check(path: Path) -> None:
    cmap = TTFont(str(path)).getBestCmap()
    need = {
        "Arabic letters": range(0x0621, 0x064B),
        "Presentation forms-B": [c for c in range(0xFE80, 0xFEFC + 1)],
        "Latin": range(0x41, 0x5B),
        "Digits": range(0x30, 0x3A),
    }
    for label, rng in need.items():
        missing = [c for c in rng if c not in cmap]
        status = "ok" if not missing else f"missing {len(missing)}"
        print(f"  {label}: {status}")


if __name__ == "__main__":
    noto = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/usr/share/fonts/truetype/noto")
    OUT.mkdir(parents=True, exist_ok=True)
    for style in ("Regular", "Bold"):
        p = build(noto, style)
        print(p, p.stat().st_size // 1024, "KB")
        check(p)
