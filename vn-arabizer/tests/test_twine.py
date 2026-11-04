import html
import re

import pytest

from fake_twine import make_twine
from vnarabizer.core import pipeline
from vnarabizer.core.project import Project
from vnarabizer.engines import detect
from vnarabizer.engines.twine import TwineEngine
from vnarabizer.translators.base import Translator


class FakeAR(Translator):
    id = "fake"

    def translate(self, texts, source, target="ar"):
        return ["عربي " + t for t in texts]


def passages(path):
    doc = path.read_text("utf-8")
    return {
        html.unescape(n): html.unescape(t)
        for n, t in re.findall(r'<tw-passagedata[^>]*name="([^"]*)"[^>]*>(.*?)</tw-passagedata>', doc, re.S)
    }


@pytest.mark.parametrize("fmt", ["SugarCube", "Harlowe"])
def test_detect(tmp_path, fmt):
    root = make_twine(tmp_path / "G", fmt)
    eng = detect(root)
    assert isinstance(eng, TwineEngine)
    assert eng.markup == fmt.lower()
    assert isinstance(detect(root / "Test Game.html"), TwineEngine)


def test_sugarcube_extract_only_text(tmp_path):
    eng = detect(make_twine(tmp_path / "G", "SugarCube"))
    entries = {(e.channel, e.original) for e in eng.extract()}
    lines = {o for c, o in entries if c == "line"}
    links = {o for c, o in entries if c == "link"}
    assert "You wake up in a small room. You have $gold coins." in lines
    assert "<<if $gold > 3>>You feel rich today!<</if>>" in lines
    assert any(l.startswith("''Mira'' smiles") for l in lines)
    assert links == {"Go to the market", "Bedroom", "Open the window", "Rest", "Back"}
    joined = " ".join(o for _, o in entries)
    for never in ("console.log", "a comment", "<<set $gold to 5>>", "Story JavaScript"):
        assert never not in joined


def test_sugarcube_apply_keeps_code_and_targets(tmp_path):
    root = make_twine(tmp_path / "G", "SugarCube")
    eng = detect(root)
    f = root / "Test Game.html"
    original = f.read_bytes()
    p = Project(root, private=True)
    p.markup = eng.markup
    p.merge_entries(eng.extract())
    st = pipeline.run(p, FakeAR(), source="en")
    assert st.errors == 0
    eng.apply(p.translations())

    ps = passages(f)
    start = ps["Start"]
    # Prose is Arabic, every macro / variable / passage name is intact.
    assert "عربي" in start
    for code in ("$gold", "<<if $gold > 3>>", "<</if>>", "<<print $name>>", "<<set $air to true>>", "<</link>>",
                 '<<goto "Bedroom">>', '<<script>>\nconsole.log("not text");\n<</script>>', "/* a comment that is not text */"):
        assert code in start, code
    assert "[[عربي Go to the market|Market]]" in start
    assert "[[عربي Bedroom|Bedroom]]" in start  # [[Target]] keeps its target
    assert '<<link "عربي Open the window" "Window">>' in start
    assert '<<button "عربي Rest">>' in start
    assert '<<return "رجوع">>' in ps["Market"]  # common word from the built-in dictionary
    assert '<span class="note">' in ps["Market"]
    assert ps["StoryInit"] == '<<set $gold to 5>><<set $name to "Mira">>'
    doc = f.read_text("utf-8")
    assert 'id="vnarabizer-rtl"' in doc and "direction:rtl" in doc
    assert doc.index('id="vnarabizer-rtl"') < doc.index("</head>")

    eng.apply(p.translations())  # re-applying starts from the original again
    assert f.read_text("utf-8").count('id="vnarabizer-rtl"') == 1

    eng.remove()
    assert f.read_bytes() == original
    assert not eng.is_applied()


def test_harlowe_links_and_hooks(tmp_path):
    root = make_twine(tmp_path / "G", "Harlowe")
    eng = detect(root)
    links = {e.original for e in eng.extract() if e.channel == "link"}
    assert links == {"Go to the market", "Go to bed", "Open the window"}
    p = Project(root, private=True)
    p.markup = eng.markup
    p.merge_entries(eng.extract())
    pipeline.run(p, FakeAR(), source="en")
    eng.apply(p.translations())
    start = passages(root / "Test Game.html")["Start"]
    assert "(set: $gold to 5)" in start and "(if: $gold > 3)[" in start
    assert "[[عربي Go to the market->Market]]" in start
    assert "[[Bedroom<-عربي Go to bed]]" in start
    assert '(link-goto: "عربي Open the window", "Window")' in start
    assert "tw-story{direction:rtl" in (root / "Test Game.html").read_text("utf-8")


def test_translator_output_cannot_inject_code(tmp_path):
    from vnarabizer.core import protect

    m = protect.mask("You have $gold coins.", "sugarcube")
    out = protect.unmask(m, "لديك ⟦0⟧ عملة <<run evil()>> $x [[a|b]]")
    assert "<<" not in out.replace("&lt;&lt;", "") or "&lt;" in out
    assert "$gold" in out and "&#36;x" in out and "&#91;&#91;" in out
    assert protect.validate(m.original, out, "sugarcube") == []
