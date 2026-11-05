"""CatSystem2 / KiriKiri / script-folder engines with a stand-in for VNTextPatch.

The real VNTextPatch (a Windows .NET tool) runs in the Windows CI build;
here a small Python stand-in reads and writes the same JSON layout so the
archive handling, Arabic preparation and install/remove logic are tested.
"""

import json
import re

import pytest

from fake_vn import CST_STRINGS, KS_TEXT, make_catsystem2_game, make_cst, make_kirikiri_game, read_cst_strings
from vnarabizer.archives.kif import KifArchive
from vnarabizer.archives.xp3 import Xp3Archive
from vnarabizer.core import arabic_visual, pipeline
from vnarabizer.core.project import Project
from vnarabizer.engines import detect
from vnarabizer.engines.vntp import CatSystem2Engine, KiriKiriEngine, ScriptFolderEngine, tool
from vnarabizer.translators.base import Translator

PASS = "FW-TEST-KEY"


class FakeAR(Translator):
    id = "fake"

    def translate(self, texts, source, target="ar"):
        return ["مرحبا بك يا صديقي العزيز في هذا العالم الجميل" for _ in texts]


def _fake_extract(orig, json_dir, fmt=None):
    json_dir.mkdir(parents=True, exist_ok=True)
    for f in orig.rglob("*"):
        if f.suffix == ".cst":
            entries, name = [], None
            for typ, text in read_cst_strings(f.read_bytes()):
                if typ == 0x21:
                    name = text
                elif typ == 0x20 and text:
                    e = {"message": text.replace("\\n", "\r\n")}
                    if name:
                        e["name"] = name
                    entries.append(e)
                    name = None
        elif f.suffix == ".ks":
            text = f.read_bytes()[2:].decode("utf-16-le")
            entries = [{"message": l.replace("[p]", "")} for l in text.split("\r\n") if l.startswith("「")]
        else:
            continue
        (json_dir / (f.stem + ".json")).write_text(json.dumps(entries, ensure_ascii=False), "utf-8")


def _fake_insert(orig, tjs, out, sjis_ext, fmt=None):
    out.mkdir(parents=True, exist_ok=True)
    for f in orig.rglob("*"):
        js = tjs / (f.stem + ".json")
        if not js.exists():
            continue
        entries = json.loads(js.read_text("utf-8"))
        if f.suffix == ".cst":
            def esc(text):
                return "".join(f"\\${ord(c)};" if ord(c) > 0x7F and c not in "「」" else c for c in text)

            strings = []
            for e in entries:
                if "name" in e:
                    strings.append((0x21, esc(e["name"])))
                strings.append((0x20, esc(e["message"])))
            (out / f.name).write_bytes(make_cst([(t, s.replace("\r\n", "\\n")) for t, s in strings]))
        else:
            (out / f.name).write_bytes(b"\xff\xfe" + "\r\n".join(e["message"] for e in entries).encode("utf-16-le"))
    sjis_ext.write_bytes(b"")


@pytest.fixture
def fake_tools(tmp_path, monkeypatch):
    tools = tmp_path / "tools"
    for name in tool.PROXIES:
        p = tools / "VNTextProxy" / f"{name}.dll"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"MZ proxy " + name.encode())
    (tools / "KirikiriTools").mkdir()
    (tools / "KirikiriTools" / "version.dll").write_bytes(b"MZ krkr")
    (tools / "VNTextPatch").mkdir()
    (tools / "VNTextPatch" / "VNTextPatch.exe").write_bytes(b"MZ")
    monkeypatch.setenv("VNARABIZER_TOOLS", str(tools))
    monkeypatch.setattr(tool, "extract", _fake_extract)
    monkeypatch.setattr(tool, "insert", _fake_insert)
    return tools


def _fake_exe(monkeypatch, root, imports):
    exe = root / "game.exe"
    exe.write_bytes(b"MZ" + b"\x00" * 64)
    monkeypatch.setattr(tool, "exe_imports", lambda e: set(imports))
    monkeypatch.setattr(tool, "is_32bit", lambda e: True)
    return exe


def _translate(eng):
    p = Project(eng.root, private=True)
    p.markup = eng.markup
    p.merge_entries(eng.extract())
    st = pipeline.run(p, FakeAR(), source="ja")
    assert st.errors == 0
    return p


@pytest.mark.parametrize("encrypted", [False, True])
def test_catsystem2_extract_apply_remove(tmp_path, fake_tools, monkeypatch, encrypted):
    root = make_catsystem2_game(tmp_path / "cs2", encrypted_passphrase=PASS if encrypted else None)
    monkeypatch.setattr("vnarabizer.engines.vntp.catsystem2.passphrase_from_exe", lambda e: PASS)
    exe = _fake_exe(monkeypatch, root, {"kernel32", "winmm", "d3d9"})
    original = (root / "scene.int").read_bytes()
    eng = detect(root)
    assert isinstance(eng, CatSystem2Engine)

    entries = eng.extract()
    names = {e.original for e in entries if e.channel == "name"}
    msgs = {e.original for e in entries if e.channel == "message"}
    assert names == {"ハロルド"}
    assert "今日は\r\nいい天気ですね。" in msgs and "さようなら。" in msgs

    p = _translate(eng)
    eng.apply(p.translations())

    arc = KifArchive(root / "scene.int")
    assert not arc.encrypted and arc.names() == ["op01.cst", "op02.cst", "startup.xml"]
    patched = read_cst_strings(arc.read("op01.cst"))
    text = "".join(s for _, s in patched)
    assert "\\$" in text  # Arabic stored as CatSystem2 escapes
    assert arc.read("startup.xml") == b"<xml/>"
    assert (root / "winmm.dll").read_bytes() == b"MZ proxy winmm"  # first proxy the exe imports
    assert (root / "VN Arabic.ttf").exists()
    assert (root / "image.int").exists()

    eng.remove()
    assert (root / "scene.int").read_bytes() == original
    assert not (root / "winmm.dll").exists() and not (root / "VN Arabic.ttf").exists()
    assert exe.exists()


def test_kirikiri_patch_xp3(tmp_path, fake_tools, monkeypatch):
    root = make_kirikiri_game(tmp_path / "krkr")
    _fake_exe(monkeypatch, root, {"version", "winmm"})
    eng = detect(root)
    assert isinstance(eng, KiriKiriEngine)
    p = _translate(eng)
    eng.apply(p.translations())
    patch = Xp3Archive(root / "patch.xp3")
    assert patch.names() == ["first.ks"]
    ks = patch.read(patch.entries[0])[2:].decode("utf-16-le")
    assert re.search("[ﹰ-﻿]", ks)  # joined Arabic glyphs
    assert (root / "version.dll").read_bytes() == b"MZ krkr"  # unencrypted-archive helper
    assert (root / "winmm.dll").exists()  # VNTextProxy avoids version.dll
    eng.remove()
    assert not (root / "patch.xp3").exists() and not (root / "version.dll").exists()
    assert (root / "data.xp3").exists()


def test_script_folder(tmp_path, fake_tools):
    folder = tmp_path / "Game" / "scene"
    folder.mkdir(parents=True)
    (folder / "op01.cst").write_bytes(make_cst())
    (folder / "readme.txt").write_text("x")
    eng = ScriptFolderEngine.detect(folder)
    assert eng is not None and "CatSystem2" in eng.describe()
    p = _translate(eng)
    eng.apply(p.translations())
    out = tmp_path / "Game" / "scene (Arabic)"
    assert (out / "op01.cst").exists()
    assert (out / "_put_next_to_game_exe" / "VN Arabic.ttf").exists()
    assert (out / "_put_next_to_game_exe" / "d2d1.dll").exists()
    eng.remove()
    assert not out.exists()


def test_visual_arabic_keeps_codes_in_order():
    tok = re.compile(r"\[[^\]\n]{1,80}\]|\\[A-Za-z@]+")
    out = arabic_visual.visual_text("مرحبا [ruby text=\"x\"] يا صديقي", 40, tok)
    assert '[ruby text="x"]' in out  # not mirrored to ]...[
    assert "ﺣ" in out or "ﻣ" in out  # joined forms present
    lines = arabic_visual.visual_text("كلمة " * 30, 10).split("\n")
    assert len(lines) > 3
