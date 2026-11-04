# PyInstaller build: one folder with the windowed app and a console tool.
#   pyinstaller packaging/vnarabizer.spec --noconfirm
# -*- mode: python -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).parent

datas = [(str(ROOT / "vnarabizer" / "assets"), "vnarabizer/assets")]
binaries = []
hiddenimports = []
for pkg in ("ctranslate2", "sentencepiece"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + ["vnarabizer.gui", "vnarabizer.gui.main_window"],
    excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia"],
    noarchive=False,
)
pyz = PYZ(a.pure)

gui = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VNArabizer",
    console=False,
    icon=str(ROOT / "packaging" / "icon.ico"),
)
cli = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="vnarabizer-cli",
    console=True,
    icon=str(ROOT / "packaging" / "icon.ico"),
)
coll = COLLECT(gui, cli, a.binaries, a.datas, name="VNArabizer")
