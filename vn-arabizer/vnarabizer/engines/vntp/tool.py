"""Bundled third-party tools: VNTextPatch, VNTextProxy, KirikiriTools.

VNTextPatch and VNTextProxy are from arcusmaximus/VNTranslationTools (MIT);
KirikiriUnencryptedArchive is from arcusmaximus/KirikiriTools (MIT). They
are built from source by the Windows build and shipped in assets/tools.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from ...core.paths import assets_dir

# Extensions VNTextPatch recognises on its own, and what to call the engine.
AUTO_FORMATS = {
    ".ws2": "AdvHD",
    ".asb": "Artemis",
    ".ast": "Artemis",
    ".cst": "CatSystem2",
    ".a0": "Cyberworks C,system",
    ".ks": "KiriKiri",
    ".scn": "KiriKiri Z (PSB/E-mote .scn)",
    ".mjo": "Majiro",
    ".sc": "Musica",
    ".nut": "Mware",
    ".msc": "Propeller",
    ".hst": "ShSystem",
    ".mes": "Silky's / AI6WIN",
    ".s": "QLIE",
    ".nnn": "SystemNNN",
    ".spt": "SystemNNN",
    ".srp": "TmrHiroAdvSystem",
    ".ybn": "YU-RIS",
}
# Formats VNTextPatch needs to be told about (no unique extension).
EXPLICIT_FORMATS = {
    "ethornell": "BGI / Ethornell",
    "reallive": "RealLive / Siglus text",
    "whale": "Whale",
    "kirikiriks": "KiriKiri (.txt scripts)",
    "artemistxt": "Artemis (.txt scripts)",
    "tmrhiroadvsystemtext": "TmrHiroAdvSystem (no extension)",
}

# VNTextProxy can pose as any of these DLLs; the game must import one of them.
PROXIES = ("d2d1", "winmm", "version", "dinput8", "d3d9", "ddraw", "xinput1_3", "msacm32")


class ToolMissing(RuntimeError):
    pass


def tools_dir() -> Path:
    override = os.environ.get("VNARABIZER_TOOLS")
    return Path(override) if override else assets_dir() / "tools"


def vntextpatch() -> Path:
    exe = tools_dir() / "VNTextPatch" / "VNTextPatch.exe"
    if not exe.exists():
        raise ToolMissing("VNTextPatch is not bundled with this build (it is included in the Windows installer).")
    return exe


def proxy_dll(name: str) -> Path:
    p = tools_dir() / "VNTextProxy" / f"{name}.dll"
    if not p.exists():
        raise ToolMissing(f"VNTextProxy ({name}.dll) is not bundled with this build.")
    return p


def kirikiri_unencrypted_dll() -> Path:
    p = tools_dir() / "KirikiriTools" / "version.dll"
    if not p.exists():
        raise ToolMissing("KirikiriUnencryptedArchive (version.dll) is not bundled with this build.")
    return p


def run(args: list[str], cwd: Path | None = None) -> str:
    exe = vntextpatch()
    cmd = [str(exe), *args]
    if sys.platform != "win32":
        cmd = ["mono", *cmd]  # development only
    flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
    p = subprocess.run(cmd, cwd=str(cwd or exe.parent), capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=flags)
    out = (p.stdout or "") + (p.stderr or "")
    if p.returncode != 0:
        raise RuntimeError(f"VNTextPatch failed ({p.returncode}): {out.strip()[-2000:]}")
    return out


def extract(scripts: Path, json_dir: Path, fmt: str | None = None) -> None:
    json_dir.mkdir(parents=True, exist_ok=True)
    run(["extractlocal", str(scripts), str(json_dir)] + ([f"--format={fmt}"] if fmt else []))


def insert(scripts: Path, json_dir: Path, out_dir: Path, sjis_ext: Path, fmt: str | None = None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    run(["insertlocal", str(scripts), str(json_dir), str(out_dir), str(sjis_ext)] + ([f"--format={fmt}"] if fmt else []))


def read_json_dir(json_dir: Path) -> dict[str, list[dict]]:
    """{relative json path: [{"name"/"names", "message"}]}"""
    out = {}
    for f in sorted(json_dir.rglob("*.json")):
        try:
            out[f.relative_to(json_dir).as_posix()] = json.loads(f.read_text("utf-8-sig"))
        except Exception:
            continue
    return out


def exe_imports(exe: Path) -> set[str]:
    import pefile

    try:
        pe = pefile.PE(str(exe), fast_load=True)
        pe.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"], pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]]
        )
    except Exception:
        return set()
    names = set()
    for attr in ("DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT"):
        for imp in getattr(pe, attr, []) or []:
            names.add(imp.dll.decode("ascii", "replace").lower().removesuffix(".dll"))
    return names


def is_32bit(exe: Path) -> bool:
    import pefile

    try:
        pe = pefile.PE(str(exe), fast_load=True)
        return pe.FILE_HEADER.Machine == 0x14C
    except Exception:
        return True


def choose_proxy(exe: Path, avoid: tuple[str, ...] = ()) -> str | None:
    imports = exe_imports(exe)
    for name in PROXIES:
        if name in imports and name not in avoid:
            return name
    return None


def find_game_exe(folder: Path) -> Path | None:
    skip = ("unins", "setup", "config", "launcher", "dxsetup", "vcredist", "notification_helper", "crash")
    exes = [p for p in folder.glob("*.exe") if not any(k in p.stem.lower() for k in skip)]
    if not exes:
        return None
    return max(exes, key=lambda p: p.stat().st_size)
