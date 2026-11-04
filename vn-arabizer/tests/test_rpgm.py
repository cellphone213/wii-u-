import json

import pytest

from fake_rpgm import make_game
from vnarabizer.core import pipeline
from vnarabizer.core.project import Project
from vnarabizer.engines import detect
from vnarabizer.engines.rpgm import PLUGIN_LINE, RPGMakerEngine
from vnarabizer.engines.rpgm.wrap import text_width, wrap
from vnarabizer.translators.base import Translator


@pytest.fixture(params=[False, True], ids=["MV", "MZ"])
def game(tmp_path, request):
    return make_game(tmp_path / "Game", mz=request.param), request.param


def test_detect(game):
    root, mz = game
    eng = detect(root)
    assert isinstance(eng, RPGMakerEngine)
    assert eng.mz == mz
    assert eng.describe() == ("RPG Maker MZ 1.8.0" if mz else "RPG Maker MV 1.6.2")
    if not mz:
        assert isinstance(detect(root / "www"), RPGMakerEngine)


def test_extract_only_visible_text(game):
    root, mz = game
    entries = {(e.channel, e.original): e for e in detect(root).extract()}
    keys = set(entries)
    assert ("message", "\\C[2]こんにちは、\\N[1]さん！\n今日は\\V[3]ゴールドを持っているね。") in keys
    assert ("message", "Hello there.\nHow are you today?") in keys
    assert ("choice", "はい") in keys and ("choice", "いいえ") in keys
    assert entries[("choice", "はい")].count == 2  # 102 list + 402 label
    assert ("db", "ハロルド") in keys and ("db", "ポーション") in keys
    assert ("db", "HPを500回復する。") in keys
    assert ("db", "はじまりの村") in keys
    assert ("system", "%1には効かなかった！") in keys and ("system", "戦う") in keys
    if mz:
        assert ("name", "ハロルド") in keys
    texts = " ".join(o for _, o in keys)
    for never in ("$gameVariables", "ShowPicture", "hero.png", "メモ", "Actor1", "<plugin:data>", "Castle"):
        assert never not in texts


class FakeAR(Translator):
    id = "fake"

    def translate(self, texts, source, target="ar"):
        return ["عربي " * 6 + t for t in texts]


def test_translate_apply_and_remove(game):
    root, mz = game
    eng = detect(root)
    web = eng.web
    originals = {p: p.read_bytes() for p in (web / "data").glob("*.json")}
    originals[web / "js" / "plugins.js"] = (web / "js" / "plugins.js").read_bytes()

    project = Project(root, private=True)
    project.markup = eng.markup
    project.merge_entries(eng.extract())
    stats = pipeline.run(project, FakeAR(), source="ja")
    assert stats.errors == 0
    eng.apply(project.translations())

    m = json.loads((web / "data" / "Map001.json").read_text("utf-8"))
    lst = m["events"][1]["pages"][0]["list"]
    lines = [c["parameters"][0] for c in lst if c["code"] == 401]
    msg = "\n".join(lines)
    # Codes survive in place, Arabic is present, lines fit the face-message width.
    assert "\\C[2]" in msg and "\\N[1]" in msg and "\\V[3]" in msg
    assert "عربي" in msg
    lay = eng.layout()
    assert all(text_width(l, lay.font_size) <= lay.message_width(True) + 1 for l in lines[:-1] if " " in l)
    # Untouched commands are byte-identical.
    assert {"code": 355, "indent": 0, "parameters": ["$gameVariables.setValue(1, 'こんにちは')"]} in lst
    choices = next(c for c in lst if c["code"] == 102)["parameters"][0]
    assert choices == ["نعم", "لا"]  # common words come from the built-in dictionary
    assert m["note"] == "<tag>"

    plugins = (web / "js" / "plugins.js").read_text("utf-8")
    assert plugins.strip().endswith(PLUGIN_LINE)
    assert plugins.count("VNArabizer_RTL") == 1
    assert (web / "js" / "plugins" / "VNArabizer_RTL.js").exists()
    assert (web / "fonts" / "VNArabic-Regular.ttf").exists()
    assert eng.is_applied()

    # Applying again does not stack plugin lines or re-translate Arabic.
    eng.apply(project.translations())
    assert (web / "js" / "plugins.js").read_text("utf-8").count("VNArabizer_RTL") == 1
    m2 = json.loads((web / "data" / "Map001.json").read_text("utf-8"))
    assert m2 == m

    eng.remove()
    assert not eng.is_applied()
    for p, data in originals.items():
        assert p.read_bytes() == data, p
    assert not (web / "js" / "plugins" / "VNArabizer_RTL.js").exists()
    assert not (web / "fonts" / "VNArabic-Regular.ttf").exists()


def test_wrap_respects_width():
    text = "هذا نص عربي طويل جداً يجب أن يُقسم على عدة أسطر حتى لا يخرج من نافذة الرسائل " * 3
    lines = wrap(text.strip(), 400, 28)
    assert len(lines) > 2
    assert all(text_width(l, 28) <= 400 for l in lines)
    assert " ".join(lines) == text.strip()


def test_rpgm_markup_protection():
    from vnarabizer.core import protect

    m = protect.mask("\\C[2]こんにちは、\\N[1]さん！\n今日は", "rpgm")
    assert m.prefix == "\\C[2]" and m.tokens == ["\\N[1]"]
    assert "\n" not in m.body  # lines are joined for the translator
    out = protect.unmask(m, "مرحبا ⟦0⟧! اليوم \\ x")
    assert out == "\\C[2]مرحبا \\N[1]! اليوم \\\\ x"
    assert protect.validate(m.original, out, "rpgm") == []
    assert protect.validate("\\V[1] gold", "ذهب", "rpgm")
