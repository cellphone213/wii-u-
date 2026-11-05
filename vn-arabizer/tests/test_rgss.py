import re
import zlib

import pytest

from fake_rgss import make_vxace_game, make_xp_game, vxace_files
from test_vntp import FakeAR
from vnarabizer.archives import rgss
from vnarabizer.archives.rmarshal import dumps, loads
from vnarabizer.core import pipeline
from vnarabizer.core.project import Project
from vnarabizer.engines import detect
from vnarabizer.engines.rgss import RGSSEngine, SCRIPT_NAME, fix_colors

PRES = re.compile("[\ufe70-\ufeff]")


@pytest.mark.parametrize("version", [1, 3])
def test_archive_roundtrip(tmp_path, version):
    files = [("Data/A.rvdata2", b"abc" * 100), ("Data/B.rvdata2", b"x"), ("Graphics/c.png", bytes(range(256)))]
    rgss.build(tmp_path / "g", files, version)
    a = rgss.RgssArchive(tmp_path / "g")
    assert a.version == version and [e.name for e in a.entries] == [n.replace("/", "\\") for n, _ in files]
    assert [a.read(e) for e in a.entries] == [d for _, d in files]
    a.write(tmp_path / "h", {"Data/B.rvdata2": b"new contents!"})
    b = rgss.RgssArchive(tmp_path / "h")
    assert [b.read(e) for e in b.entries] == [b"abc" * 100, b"new contents!", bytes(range(256))]


def test_marshal_roundtrip_all_fixture_files():
    for name, data in vxace_files().items():
        if name.endswith("rvdata2"):
            assert dumps(loads(data)) == data, name


def test_fix_colors():
    # visual "B\C[0]A\C[2]": A (logically first) is red, B normal
    assert fix_colors(["B\\C[0]A\\C[2]"]) == ["\\C[0]B\\C[2]A"]
    assert fix_colors(["plain"]) == ["plain"]


def _translate(eng):
    p = Project(eng.root, private=True)
    p.markup = eng.markup
    entries = eng.extract()
    p.merge_entries(entries)
    assert pipeline.run(p, FakeAR(), source="ja").errors == 0
    return p, entries


@pytest.mark.parametrize("archive", [True, False])
def test_vxace(tmp_path, archive):
    root = make_vxace_game(tmp_path / "vxa", archive)
    original = (root / "Game.rgss3a").read_bytes() if archive else (root / "Data/Map001.rvdata2").read_bytes()
    eng = detect(root)
    assert isinstance(eng, RGSSEngine) and eng.rm_version == "vxace"
    p, entries = _translate(eng)
    by = {(e.channel, e.original) for e in entries}
    assert ("message", "\\N[1]「こんにちは、世界！」\n今日は\\C[2]いい天気\\C[0]ですね。") in by
    assert ("choice", "はい") in by and ("name", "ハロルド") in by
    assert ("db", "森の小道") in by and ("db", "戦う") in by and ("db", "テストゲーム") in by
    assert ("vocab", "%1$sは%2$sを使った！") in by and ("vocab", "お金を %1$s\\G 手に入れた！") in by
    assert not any("テスト'" in o or "EV001" in o or "<tag>" in o or "<memo>" in o for _, o in by)

    written = eng.apply(p.translations())
    assert "Fonts/VN Arabic.ttf" in written and (root / "Fonts" / "VN Arabic.ttf").exists()

    def data(name):
        if archive:
            return rgss.RgssArchive(root / "Game.rgss3a").read(name)
        return (root / name).read_bytes()

    mp = loads(data("Data/Map001.rvdata2"))
    cmds = mp.get("@events").pairs[0][1].get("@pages").items[0].get("@list").items
    codes = [c.get("@code") for c in cmds]
    assert codes[0] == 101 and codes[1] == 401 and 102 in codes and 355 in codes
    lines = [c.get("@parameters").items[0].text for c in cmds if c.get("@code") == 401]
    assert lines and all(PRES.search(l) for l in lines)
    assert cmds[codes.index(355)].get("@parameters").items[0].text == "$game_variables[1] = 'テスト'"
    assert PRES.search(mp.get("@display_name").text)
    assert mp.get("@note").text == "<memo>"

    scripts = loads(data("Data/Scripts.rvdata2")).items
    names = [x.items[1].text for x in scripts]
    assert names == ["Vocab", SCRIPT_NAME, "Main"]
    vocab = zlib.decompress(scripts[0].items[2].data).decode("utf-8")
    assert "def self.basic(id)" in vocab
    use_item = re.search(r'UseItem\s+= "(.*)"', vocab).group(1)
    assert "%1$s" in use_item and "%2$s" in use_item
    assert "%2$s" in vocab and '\\\\G' in vocab and PRES.search(vocab)
    runtime = zlib.decompress(scripts[1].items[2].data).decode("utf-8")
    assert 'Font.default_name = ["VN Arabic"]' in runtime

    # Applying again starts from the backup: still exactly one runtime script.
    eng.apply(p.translations())
    assert [x.items[1].text for x in loads(data("Data/Scripts.rvdata2")).items].count(SCRIPT_NAME) == 1

    eng.remove()
    restored = (root / "Game.rgss3a").read_bytes() if archive else (root / "Data/Map001.rvdata2").read_bytes()
    assert restored == original and not (root / "Fonts").exists()


def test_xp_first_line_in_101(tmp_path):
    root = make_xp_game(tmp_path / "xp")
    eng = detect(root)
    assert isinstance(eng, RGSSEngine) and eng.rm_version == "xp"
    p, entries = _translate(eng)
    assert {e.original for e in entries if e.channel == "message"} == {"「こんにちは」\n元気？"}
    eng.apply(p.translations())
    ce = loads(rgss.RgssArchive(root / "Game.rgssad").read("Data/CommonEvents.rxdata"))
    cmds = ce.items[1].get("@list").items
    assert cmds[0].get("@code") == 101 and PRES.search(cmds[0].get("@parameters").items[0].text)
    assert all(c.get("@code") == 401 for c in cmds[1:-1]) and cmds[-1].get("@code") == 0
    assert cmds[0].get("@parameters").items[0].ivars is None  # Ruby 1.8 strings stay plain
