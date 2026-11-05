"""Name the engine of a game VN Arabizer cannot patch, and say why.

Used when ``detect`` finds nothing, so the user gets "this is Malie (its
.dat files are encrypted per game)" instead of a bare "unknown engine".
Only cheap checks: file names, folder names and a few magic bytes.
"""

from __future__ import annotations

from pathlib import Path

from .vntp.base import BACKUP_DIR

# (engine, reason in Arabic)
_NOT_SUPPORTED = "غير مدعوم حالياً"


def _magic(f: Path, n: int = 16) -> bytes:
    try:
        with open(f, "rb") as h:
            return h.read(n)
    except OSError:
        return b""


def _files(root: Path, pattern: str, depth2: bool = True) -> list[Path]:
    out = [p for p in root.glob(pattern) if p.is_file()]
    if depth2:
        out += [p for p in root.glob("*/" + pattern) if p.is_file() and BACKUP_DIR not in p.parts]
    return out


def identify(root: Path) -> tuple[str, str] | None:
    """(engine name, Arabic explanation) for a recognised but unsupported game, else None."""
    root = Path(root)
    if root.is_file():
        root = root.parent
    if not root.is_dir():
        return None
    names = {p.name.lower() for p in root.iterdir()}
    exes = [n for n in names if n.endswith(".exe")]

    def has(*globs: str) -> list[Path]:
        out: list[Path] = []
        for g in globs:
            out += _files(root, g)
        return out

    # -- engines with their own formats ------------------------------------------------
    if has("*.wolf") or "data.wolf" in names:
        return ("WOLF RPG Editor", f"{_NOT_SUPPORTED}: ملفات .wolf مشفّرة بصيغة خاصة.")
    if "unityplayer.dll" in names or any(p.is_dir() and p.name.lower().endswith("_data") and (p / "globalgamemanagers").exists() for p in root.iterdir()):
        return ("Unity", f"{_NOT_SUPPORTED}: نصوص Unity داخل ملفات assets وتحتاج أداة خاصة بكل لعبة.")
    if (root / "resources" / "app.asar").exists() or (root / "data" / "scenario").is_dir():
        return ("TyranoScript / Electron", f"{_NOT_SUPPORTED} مباشرة: النصوص في ملفات .ks داخل app.asar.")
    if any("malie" in n for n in names) or has("*.lib") and any(_magic(p, 4) == b"LIBP" for p in has("*.lib")):
        return ("Malie", f"{_NOT_SUPPORTED}: ملفات .dat مشفّرة بمفتاح Camellia خاص بكل لعبة.")
    if (root / "AdvData").is_dir() or (root / "advdata").is_dir() or any(n.startswith("advdata") for n in names):
        return ("Circus", f"{_NOT_SUPPORTED}: سكربتات MES وأرشيفات .dat الخاصة بـ Circus.")
    if (root / "GameData").is_dir() and _files(root / "GameData", "*.pack", False):
        return ("QLIE", f"{_NOT_SUPPORTED} مباشرة: أرشيف .pack مشفّر. إذا استخرجت سكربتات .s افتح مجلدها.")
    if has("*.npa"):
        return ("Nitroplus (NPA)", f"{_NOT_SUPPORTED}.")
    if has("*.ain", "*.ald", "*.afa"):
        return ("AliceSoft System 3.x / 4.x", f"{_NOT_SUPPORTED}.")
    if has("*.cpz"):
        return ("CMVS (Purple Software)", f"{_NOT_SUPPORTED}: أرشيف .cpz مشفّر.")
    if has("*.rld"):
        return ("ExHIBIT", f"{_NOT_SUPPORTED}.")
    if has("*.vndat"):
        return ("Light.vn", f"{_NOT_SUPPORTED}.")
    if "seen.txt" in names:
        return ("RealLive / AVG32", f"{_NOT_SUPPORTED} مباشرة: Seen.txt أرشيف مضغوط بمفتاح.")
    if has("*.xfl"):
        return ("Liar-soft", f"{_NOT_SUPPORTED}.")
    if has("*.ykc"):
        return ("Yuka", f"{_NOT_SUPPORTED}.")
    if has("*.lpk"):
        return ("Lucifen", f"{_NOT_SUPPORTED}.")
    if has("*.alf", "*.agf"):
        return ("Eushully", f"{_NOT_SUPPORTED}.")
    if has("*.war"):
        return ("ShiinaRio", f"{_NOT_SUPPORTED}.")
    if has("*.noa") and any(_magic(p, 6) == b"Entis\x1a" for p in has("*.noa")):
        return ("Entis GLS", f"{_NOT_SUPPORTED}.")
    if any(_magic(p, 8) == b"ESC-ARC2" or _magic(p, 7) == b"ESC-ARC" for p in has("*.bin")):
        return ("Escude", f"{_NOT_SUPPORTED}.")
    if any(n.startswith("arc") and n.endswith(".dat") for n in names):
        return ("Cyberworks C,system", f"{_NOT_SUPPORTED} مباشرة: إذا استخرجت سكربتات .a0 افتح مجلدها.")
    # -- known archive types whose scripts we could not find --------------------------
    if has("*.ypf"):
        return ("YU-RIS", "لم أجد سكربتات .ybn قابلة للقراءة داخل ملفات .ypf (نسخة أرشيف غير معروفة أو السكربتات داخل ملف exe).")
    if has("*.pfs"):
        return ("Artemis", "لم أجد سكربتات .ast/.asb داخل ملفات .pfs.")
    if any(_magic(p, 12) in (b"PackFile    ", b"BURIKO ARC20") for p in has("*.arc")):
        return ("BGI / Ethornell", "لم أجد سكربتات بصيغة BurikoCompiledScript (ربما صيغة BGI قديمة جداً).")
    if any(_magic(p, 10) == b"MajiroArcV" for p in has("*.arc")):
        return ("Majiro", "لم أجد ملفات .mjo داخل أرشيفات Majiro.")
    if has("*.pac"):
        return ("Softpal (?)", "لم أجد SCRIPT.SRC و TEXT.DAT و POINT.DAT داخل ملفات .pac.")
    if has("*.xp3"):
        return ("KiriKiri", "أرشيفات .xp3 مشفّرة بطريقة خاصة باللعبة.")
    dats = has("*.dat")
    if dats:
        return ("محرك يستخدم ملفات .dat", "لم أتعرف على هذا المحرك. أرسل أسماء الملفات الموجودة في مجلد اللعبة لإضافة دعمه.")
    if exes and not names - set(exes):
        return ("?", "لا يوجد في المجلد غير ملف التشغيل: ربما البيانات مدمجة فيه.")
    return None
