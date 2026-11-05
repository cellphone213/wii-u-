import re

import pytest

from fake_jp import SIGLUS_KEY, make_nscripter_game, make_siglus_game
from test_vntp import FakeAR, fake_tools, _fake_exe  # noqa: F401  (fixtures)
from vnarabizer.archives.siglus import ScenePck, lzss_pack, lzss_unpack
from vnarabizer.core import pipeline
from vnarabizer.core.project import Project
from vnarabizer.core.sjis_tunnel import SjisTunnel
from vnarabizer.engines import detect
from vnarabizer.engines.nscripter import NScripterEngine, classify
from vnarabizer.engines.siglus import SiglusEngine

PRES = re.compile("[ﹰ-﻿]")


def _translate(eng):
    p = Project(eng.root, private=True)
    p.markup = eng.markup
    p.merge_entries(eng.extract())
    assert pipeline.run(p, FakeAR(), source="ja").errors == 0
    return p


def test_nscripter_classify():
    assert classify("【ハロルド】「こんにちは」@") == "text"
    assert classify('select "はい",*yes,') == "choice"
    assert classify('       "いいえ",*no') == "choice"
    assert classify('bg "bg\\room.jpg",1') == "code"
    assert classify("*start") == "code" and classify("; コメント") == "code"
    assert classify("#FF0000よかった！@") == "text"


@pytest.mark.parametrize("plain", [False, True])
def test_nscripter_roundtrip(tmp_path, fake_tools, monkeypatch, plain):
    root = make_nscripter_game(tmp_path / "ns", plain_txt=plain)
    script = root / ("0.txt" if plain else "nscript.dat")
    original = script.read_bytes()
    _fake_exe(monkeypatch, root, {"winmm", "ddraw"})
    eng = detect(root)
    assert isinstance(eng, NScripterEngine)
    texts = {(e.channel, e.original) for e in eng.extract()}
    assert ("line", "【ハロルド】「こんにちは、世界！」@") in texts
    assert ("choice", "はい") in texts and ("choice", "テストゲーム") in texts
    assert not any("room.jpg" in o for _, o in texts)

    p = _translate(eng)
    eng.apply(p.translations())
    tunnel = SjisTunnel((root / "sjis_ext.bin").read_bytes())
    data = script.read_bytes()
    raw = data if plain else bytes(b ^ 0x84 for b in data)
    text = tunnel.decode(raw)
    assert PRES.search(text)
    assert 'bg "bg\\room.jpg",1' in text and "*yes" in text and "end" in text
    lines = text.split("\r\n")
    harold = [l for l in lines if l.endswith("@") and PRES.search(l)]
    assert harold  # click wait stays at the end of the message
    assert (root / "winmm.dll").exists() and (root / "VN Arabic.ttf").exists()
    eng.remove()
    assert script.read_bytes() == original
    assert not (root / "sjis_ext.bin").exists()


def test_siglus_lzss_roundtrip():
    data = "こんにちは".encode("utf-16-le") * 20
    assert lzss_unpack(lzss_pack(data)) == data


@pytest.mark.parametrize("encrypted", [False, True])
def test_siglus_roundtrip(tmp_path, fake_tools, monkeypatch, encrypted):
    root = make_siglus_game(tmp_path / "sg", encrypted)
    if encrypted:
        (root / "siglus_key.txt").write_text(SIGLUS_KEY.hex(" "))
    original = (root / "Scene.pck").read_bytes()
    _fake_exe(monkeypatch, root, {"d3d9", "winmm"})
    eng = detect(root)
    assert isinstance(eng, SiglusEngine)
    texts = {e.original for e in eng.extract()}
    assert texts == {"【ハロルド】", "「こんにちは、世界！」", "今日はいい天気ですね。", "さようなら。"}  # "bg01" is code

    p = _translate(eng)
    eng.apply(p.translations())
    pck = ScenePck((root / "Scene.pck").read_bytes(), SIGLUS_KEY if encrypted else None)
    s01 = pck.scenes[0].strings
    assert s01[2] == "bg01" and len(s01) == 4
    assert PRES.search(s01[1])
    eng.remove()
    assert (root / "Scene.pck").read_bytes() == original


def test_siglus_needs_key(tmp_path, fake_tools):
    root = make_siglus_game(tmp_path / "sg", True)
    with pytest.raises(RuntimeError, match="not bundled|key"):
        detect(root).extract()
