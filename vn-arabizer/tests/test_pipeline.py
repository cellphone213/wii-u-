from vnarabizer.core import pipeline
from vnarabizer.core.project import Project
from vnarabizer.engines.base import Entry
from vnarabizer.translators.base import Translator


class FakeAR(Translator):
    """Pretends to translate; can be told to lose placeholders like a real MT engine."""

    id = "fake"
    batch_size = 4

    def __init__(self, keeps=True, lose_placeholders=False):
        super().__init__()
        self.keeps_placeholders = keeps
        self.lose = lose_placeholders
        self.calls = []

    def translate(self, texts, source, target="ar"):
        self.calls.append((source, list(texts)))
        out = []
        for t in texts:
            if self.lose:
                t = t.replace("⟦", "").replace("⟧", "")
            out.append("عربي " + t)
        return out


def make_project(tmp_path, texts):
    p = Project(tmp_path, private=True)
    p.merge_entries(Entry("say", t, "dialogue", order=i) for i, t in enumerate(texts))
    return p


def test_markup_survives_even_when_translator_destroys_placeholders(tmp_path):
    p = make_project(tmp_path, ["Hi [player], {b}look{/b}!", "Plain line.", "{w}"])
    stats = pipeline.run(p, FakeAR(keeps=True, lose_placeholders=True), source="en")
    rows = {r.original: r for r in p.rows()}
    out = rows["Hi [player], {b}look{/b}!"].translation
    assert rows["Hi [player], {b}look{/b}!"].status == "machine"
    assert out.count("[player]") == 1 and "{b}" in out and "{/b}" in out
    assert rows["Plain line."].translation == "عربي Plain line."
    assert rows["{w}"].status == "locked"
    assert stats.errors == 0


def test_duplicates_are_translated_once(tmp_path):
    p = make_project(tmp_path, ["Yes.", "No.", "Maybe."])
    t = FakeAR()
    pipeline.run(p, t, source="en")
    sent = [x for _, batch in t.calls for x in batch]
    assert len(sent) == len(set(sent))


def test_glossary_names_are_fixed(tmp_path):
    p = make_project(tmp_path, ["Eileen is here.", "Eileen"])
    p.set_glossary("Eileen", "آيلين")
    pipeline.run(p, FakeAR(keeps=False), source="en")
    rows = {r.original: r for r in p.rows()}
    assert rows["Eileen"].translation == "آيلين"
    assert "آيلين" in rows["Eileen is here."].translation
    assert "Eileen" not in rows["Eileen is here."].translation


def test_auto_language_groups_batches(tmp_path):
    p = make_project(tmp_path, ["こんにちは", "Hello"])
    t = FakeAR()
    pipeline.run(p, t, source="auto")
    langs = sorted(l for l, _ in t.calls)
    assert langs == ["en", "ja"]


def test_translations_feed_the_patch(tmp_path):
    p = make_project(tmp_path, ["Hello"])
    pipeline.run(p, FakeAR(), source="en")
    assert p.translations() == {("say", "Hello"): "عربي Hello"}


def test_punctuation_around_tags_is_kept(tmp_path):
    p = make_project(tmp_path, ["Hello, [player]! Welcome to {b}Ren'Py{/b}."])
    t = FakeAR(keeps=False)
    pipeline.run(p, t, source="en")
    out = p.rows()[0].translation
    sent = [x for _, batch in t.calls for x in batch]
    assert "Hello" in sent and "Welcome to" in sent  # only words go to the translator
    assert "عربي Hello، [player]! عربي Welcome to {b}" in out
    assert out.endswith("{/b}.")
