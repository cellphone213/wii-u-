import json
import re

import pytest

import fake_packs as fp
from test_vntp import FakeAR, fake_tools, _fake_exe  # noqa: F401
from vnarabizer.archives import packs
from vnarabizer.core import pipeline
from vnarabizer.core.project import Project
from vnarabizer.engines import detect
from vnarabizer.engines.vntp import packed, tool

PRES = re.compile("[ﹰ-﻿]")
OTHER = ("bg01.png", b"\x89PNG" + bytes(range(256)) * 3)


def test_dsc_roundtrip():
    data = fp.script(fp.BURIKO, fp.LINES * 3)
    enc = fp.dsc_encode(data)
    assert len(enc) < len(data) + 0x240
    assert packs.dsc_decode(enc) == data
    assert packs.dsc_decode(enc, 32) == data[:32]


def _fake_extract(orig, js, fmt=None):
    js.mkdir(parents=True, exist_ok=True)
    for f in orig.rglob("*"):
        if f.is_file():
            lines = f.read_bytes().split(b"\n")[1:]
            msgs = [{"message": l[3:].decode("utf-8")} for l in lines if l.startswith(b"JP:")]
            if msgs:
                (js / (f.relative_to(orig).as_posix().replace("/", "__") + ".json")).write_text(json.dumps(msgs, ensure_ascii=False), "utf-8")


def _fake_insert(orig, tjs, out, sjis_ext, fmt=None):
    for f in orig.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(orig)
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        js = tjs / (rel.as_posix().replace("/", "__") + ".json")
        if not js.exists():
            target.write_bytes(f.read_bytes())
            continue
        msgs = iter(json.loads(js.read_text("utf-8")))
        lines = f.read_bytes().split(b"\n")
        target.write_bytes(b"\n".join(b"JP:" + next(msgs)["message"].encode("utf-8") if l.startswith(b"JP:") else l for l in lines))
    sjis_ext.write_bytes(b"\x00\x01")


@pytest.fixture
def tools(fake_tools, monkeypatch):
    monkeypatch.setattr(tool, "extract", _fake_extract)
    monkeypatch.setattr(tool, "insert", _fake_insert)
    return fake_tools


def _run(root, monkeypatch, engine_cls, archive, reader):
    """Detect, translate, apply, check, remove. ``reader(path)`` -> {name: contents}."""
    _fake_exe(monkeypatch, root, {"winmm"})
    original = archive.read_bytes()
    before = reader(archive)
    eng = detect(root)
    assert isinstance(eng, engine_cls), eng
    entries = eng.extract()
    assert {e.original for e in entries} == set(fp.LINES)
    p = Project(eng.root, private=True)
    p.markup = eng.markup
    p.merge_entries(entries)
    assert pipeline.run(p, FakeAR(), source="ja").errors == 0
    written = eng.apply(p.translations())
    assert archive.name in written and "sjis_ext.bin" in written
    after = reader(archive)
    assert set(after) == set(before)
    changed = [n for n in before if after[n] != before[n]]
    assert changed, "no script changed"
    for n in changed:
        assert PRES.search(after[n].decode("utf-8")), n
        assert after[n].split(b"\n")[0] == before[n].split(b"\n")[0]  # header kept
    for n in before:
        if n not in changed:
            assert after[n] == before[n]
    assert not list(root.glob("*.vnarabizer-tmp"))
    eng.remove()
    assert archive.read_bytes() == original
    return changed


def _reader(cls):
    def read(path):
        pk = cls(path)
        return {e.name: pk.read(e) for e in pk.entries}
    return read


@pytest.mark.parametrize("v2", [False, True])
@pytest.mark.parametrize("compressed", [False, True])
def test_bgi(tmp_path, tools, monkeypatch, v2, compressed):
    root = tmp_path / "bgi"
    root.mkdir()
    s = fp.script(fp.BURIKO)
    files = [("_bgiconf", b"\0" * 40), ("scene01", fp.dsc_encode(s) if compressed else s), ("readme.txt", b"hi")]
    fp.make_bgi_arc(root / "data01000.arc", files, v2)
    fp.make_bgi_arc(root / "data02000.arc", [("bg01", b"CompressedBG___" + b"\0" * 64)], v2)
    changed = _run(root, monkeypatch, packed.BGIEngine, root / "data01000.arc", _reader(packs.BgiArc))
    assert changed == ["scene01"]


@pytest.mark.parametrize("version", [1, 2, 3])
def test_majiro(tmp_path, tools, monkeypatch, version):
    root = tmp_path / "mj"
    root.mkdir()
    fp.make_majiro_arc(root / "scenario.arc", [("start.mjo", fp.script(b"MajiroObjX1.000")), ("x.txt", b"abc")], version)
    assert _run(root, monkeypatch, packed.MajiroEngine, root / "scenario.arc", _reader(packs.MajiroArc)) == ["start.mjo"]


def test_advhd(tmp_path, tools, monkeypatch):
    root = tmp_path / "adv"
    root.mkdir()
    fp.make_advhd_arc(root / "Rio.arc", [("00_start.ws2", fp.script(b"\x00ws2")), OTHER])
    arc = packs.AdvHdArc(root / "Rio.arc")
    assert arc.raw(arc.entries[0]) != fp.script(b"\x00ws2")  # stored rotated
    assert _run(root, monkeypatch, packed.AdvHDEngine, root / "Rio.arc", _reader(packs.AdvHdArc)) == ["00_start.ws2"]


@pytest.mark.parametrize("version", [6, 8])
def test_artemis(tmp_path, tools, monkeypatch, version):
    root = tmp_path / "art"
    root.mkdir()
    fp.make_pfs(root / "root.pfs", [("script/main.ast", fp.script(b"astver = 2.0")), ("image/bg.png", OTHER[1] * 50)], version)
    arc = packs.PfsArc(root / "root.pfs")
    if version == 8:
        assert arc.raw(arc.entries[0]) != arc.read(arc.entries[0])
    assert _run(root, monkeypatch, packed.ArtemisEngine, root / "root.pfs", _reader(packs.PfsArc)) == ["script/main.ast"]


def test_artemis_patch_archive_wins(tmp_path, tools, monkeypatch):
    root = tmp_path / "art"
    root.mkdir()
    fp.make_pfs(root / "root.pfs", [("script/main.ast", fp.script(b"astver = 2.0", ["古い"])), OTHER])
    fp.make_pfs(root / "root.pfs.000", [("script/main.ast", fp.script(b"astver = 2.0"))])
    assert _run(root, monkeypatch, packed.ArtemisEngine, root / "root.pfs.000", _reader(packs.PfsArc)) == ["script/main.ast"]


def test_yuris(tmp_path, tools, monkeypatch):
    root = tmp_path / "yu"
    root.mkdir()
    files = [("ysbin/ysc.ybn", b"YSCM\n"), ("ysbin/yst00001.ybn", fp.script(b"YSTB")), OTHER]
    fp.make_ypf(root / "ysbin.ypf", files)
    arc = packs.YpfArc(root / "ysbin.ypf")
    assert [e.name for e in arc.entries] == [n for n, _ in files]
    assert _run(root, monkeypatch, packed.YurisEngine, root / "ysbin.ypf", _reader(packs.YpfArc)) == ["ysbin/yst00001.ybn"]
    arc = packs.YpfArc(root / "ysbin.ypf")
    e = arc.entries[1]
    import zlib
    assert arc._stored_sum(e) == zlib.adler32(arc.raw(e))  # checksum updated


def test_softpal(tmp_path, tools, monkeypatch):
    root = tmp_path / "sp"
    root.mkdir()
    text = b"$" + b"\0" * 15 + "こんにちは".encode("cp932") * 4
    files = [("SCRIPT.SRC", fp.script(b"Sv20")), ("TEXT.DAT", fp.softpal_encrypt(text)), ("POINT.DAT", b"$POINT_LIST_****"), OTHER]
    fp.make_softpal_pac(root / "data.pac", files)
    arc = packs.SoftpalPac(root / "data.pac")
    assert arc.read(arc.entries[1]) == text
    assert _run(root, monkeypatch, packed.SoftpalEngine, root / "data.pac", _reader(packs.SoftpalPac)) == ["SCRIPT.SRC"]
    assert (root / "data.pac").read_bytes().endswith(b"EOF \x00\x00\x00\x00")


def test_kaguya(tmp_path, tools, monkeypatch):
    root = tmp_path / "kg"
    root.mkdir()
    (root / "message.dat").write_bytes(fp.script(b"[SCR-MESSAGE]ver4.0"))
    _fake_exe(monkeypatch, root, {"winmm"})
    eng = detect(root)
    assert isinstance(eng, packed.KaguyaEngine)
    p = Project(eng.root, private=True)
    p.merge_entries(eng.extract())
    pipeline.run(p, FakeAR(), source="ja")
    eng.apply(p.translations())
    assert PRES.search((root / "message.dat").read_bytes().decode("utf-8"))
    eng.remove()
    assert (root / "message.dat").read_bytes() == fp.script(b"[SCR-MESSAGE]ver4.0")


def test_not_a_pack(tmp_path):
    f = tmp_path / "x.arc"
    f.write_bytes(b"\x01\x00\x00\x00garbage" * 10)
    assert packs.open_pack(f) is None


def test_identify(tmp_path):
    from vnarabizer.engines.identify import identify

    def game(name, files):
        root = tmp_path / name
        for rel, data in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(data)
        return identify(root)

    assert game("malie", {"malie.exe": b"MZ", "data1.dat": b"\x12" * 64})[0] == "Malie"
    assert game("qlie", {"GameData/data0.pack": b"x", "game.exe": b"MZ"})[0] == "QLIE"
    assert game("unknown_dat", {"foo.dat": b"x", "game.exe": b"MZ"})[0] == "محرك يستخدم ملفات .dat"
    assert game("empty", {"readme.txt": b"x"}) is None


def test_kirikiri_descramble():
    from vnarabizer.engines.vntp.kirikiri import descramble
    import zlib

    text = "*start\r\n「こんにちは」[p]\r\n".encode("utf-16-le")
    # mode 1: swap neighbouring bits of every UTF-16 unit (self-inverse)
    units = bytearray(text)
    for i in range(0, len(units), 2):
        c = units[i] | units[i + 1] << 8
        c = ((c & 0xAAAA) >> 1) | ((c & 0x5555) << 1)
        units[i], units[i + 1] = c & 0xFF, c >> 8
    assert descramble(b"\xfe\xfe\x01\xff\xfe" + bytes(units)) == b"\xff\xfe" + text
    # mode 2: zlib
    z = zlib.compress(text)
    blob = b"\xfe\xfe\x02\xff\xfe" + len(z).to_bytes(8, "little") + len(text).to_bytes(8, "little") + z
    assert descramble(blob) == b"\xff\xfe" + text
    # mode 0: inverse of the descrambler
    enc = bytearray(text)
    for i in range(0, len(enc), 2):
        if enc[i + 1] == 0 and enc[i] < 0x20:
            continue
        enc[i] ^= 1
        enc[i + 1] ^= enc[i] & 0xFE
    assert descramble(b"\xfe\xfe\x00\xff\xfe" + bytes(enc)) == b"\xff\xfe" + text
