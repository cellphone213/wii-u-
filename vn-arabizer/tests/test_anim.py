import re

from fake_anim import DEFINE_TEXTS, HEADER, STRINGS, define_body, make_anim_game, sce_body
from test_vntp import FakeAR, fake_tools, _fake_exe  # noqa: F401  (fixtures)
from vnarabizer.archives import anim
from vnarabizer.core import pipeline
from vnarabizer.core.project import Project
from vnarabizer.core.sjis_tunnel import SjisTunnel
from vnarabizer.engines import detect
from vnarabizer.engines.anim import AnimEngine, source_text

PRES = re.compile("[ﹰ-﻿]")


def test_cipher_roundtrip_all_lengths():
    for n in (0, 1, 14, 15, 16, 17, 31, 32, 33, 100):
        body = bytes((i * 37 + 5) & 0xFF for i in range(n))
        enc = anim.encrypt(body, HEADER)
        assert enc[:20] == HEADER and anim.decrypt(enc) == body


def test_sce_split_join_is_lossless():
    raw = anim.encrypt(sce_body(), HEADER)
    sc = anim.SceScript.load(raw)
    assert sc.dump() == raw
    assert [s.decode("cp932") for s in sc.strings[: len(STRINGS)]] == STRINGS


def test_source_text_rules():
    assert source_text(b"BG020") is None and source_text(b"") is None
    assert source_text("「漢字＜かんじ＞だ」".encode("cp932")) == "「漢字だ」"
    assert source_text("みやび".encode("cp932")) == "みやび"


def test_define_strings():
    df = anim.DefineFile.load(anim.encrypt(define_body(), HEADER))
    assert df.texts() == DEFINE_TEXTS  # "product" (ASCII) is not text
    assert df.body() == define_body()


def test_anim_end_to_end(tmp_path, fake_tools, monkeypatch):
    root = make_anim_game(tmp_path / "anim")
    _fake_exe(monkeypatch, root, {"kernel32", "winmm"})
    originals = {f: (root / f).read_bytes() for f in ("zz_sce.dat", "zz_define.dat")}
    eng = detect(root)
    assert isinstance(eng, AnimEngine)
    entries = eng.extract()
    texts = {e.original for e in entries}
    assert "「鬼灯連嗣君。ようこそ」" in texts  # furigana dropped
    assert "『迎えに来たんだ@n　お嫁さんになって下さい』" in texts and "みやび" in texts
    assert set(DEFINE_TEXTS) <= texts
    assert not any(t in texts for t in ("BGB", "w000001d", "Mmiyabi030", "product"))

    p = Project(eng.root, private=True)
    p.markup = eng.markup
    p.merge_entries(entries)
    assert pipeline.run(p, FakeAR(), source="ja").errors == 0
    written = eng.apply(p.translations())
    assert {"zz_sce.dat", "zz_define.dat", "sjis_ext.bin", "winmm.dll", "VN Arabic.ttf"} <= set(written)

    tunnel = SjisTunnel((root / "sjis_ext.bin").read_bytes())
    before = anim.SceScript.load(originals["zz_sce.dat"])
    after = anim.SceScript.load((root / "zz_sce.dat").read_bytes())
    assert after.header == before.header and after.code == before.code
    assert len(after.strings) == len(before.strings)
    for old, new in zip(before.strings, after.strings):
        if source_text(old) is None:
            assert new == old  # resource names untouched, same index
        elif old.strip(b"\x81\x48"):  # everything but "？？？"
            assert b"\0" not in new and PRES.search(tunnel.decode(new))
    multi = tunnel.decode(after.strings[4])
    assert "@n" in multi and all(PRES.search(part) for part in multi.split("@n") if part.strip())

    body = anim.decrypt((root / "zz_define.dat").read_bytes())
    assert body.startswith(define_body()[:12])  # opcode + "product" untouched
    assert body.endswith(define_body()[-16:])

    eng.remove()
    for f, data in originals.items():
        assert (root / f).read_bytes() == data
    assert not (root / "winmm.dll").exists()
