"""NLLB plumbing test with a tiny SentencePiece model and a stand-in CTranslate2.

The real model is 1+ GB and is downloaded on the user's machine; this test
checks the token format sent to CTranslate2 and the decoding of its output.
"""

import sys
import types

import pytest

sentencepiece = pytest.importorskip("sentencepiece")


@pytest.fixture
def tiny_model(tmp_path, monkeypatch):
    monkeypatch.setenv("VNARABIZER_HOME", str(tmp_path / "home"))
    corpus = tmp_path / "corpus.txt"
    corpus.write_text("\n".join(["hello world", "the sun was setting", "مرحبا بالعالم", "كانت الشمس تغرب"] * 50), "utf-8")
    from vnarabizer.translators import models

    d = models.model_path("nllb-600m")
    d.mkdir(parents=True)
    sentencepiece.SentencePieceTrainer.train(
        input=str(corpus), model_prefix=str(d / "sentencepiece.bpe"), vocab_size=60, model_type="bpe", character_coverage=1.0
    )
    (d / "sentencepiece.bpe.vocab").unlink()
    (d / "model.bin").write_bytes(b"x")
    (d / "config.json").write_text("{}")
    (d / "shared_vocabulary.json").write_text("[]")
    return d


def test_nllb_token_format(tiny_model, monkeypatch):
    seen = {}
    sp = sentencepiece.SentencePieceProcessor(model_file=str(tiny_model / "sentencepiece.bpe.model"))

    class FakeResult:
        def __init__(self, toks):
            self.hypotheses = [toks]

    class FakeTranslator:
        def __init__(self, path, device, compute_type):
            seen["device"] = device

        def translate_batch(self, batch, target_prefix, **kw):
            seen.setdefault("batches", []).append(batch)
            assert all(p == ["arb_Arab"] for p in target_prefix)
            return [FakeResult(["arb_Arab"] + sp.encode("كانت الشمس تغرب", out_type=str)) for _ in batch]

    fake = types.SimpleNamespace(Translator=FakeTranslator, get_cuda_device_count=lambda: 0)
    monkeypatch.setitem(sys.modules, "ctranslate2", fake)

    from vnarabizer.translators.nllb import NLLBTranslator

    t = NLLBTranslator("nllb-600m")
    assert t.ready()[0]
    out = t.translate(["the sun was setting"], "en")
    assert out == ["كانت الشمس تغرب"]
    batch = seen["batches"][-1][0]
    assert batch[0] == "eng_Latn" and batch[-1] == "</s>"
    assert seen["device"] == "cpu"


def test_long_lines_are_split_into_sentences():
    from vnarabizer.translators.nllb import _split_sentences

    text = "これは長い文です。" * 40
    parts = _split_sentences(text)
    assert len(parts) > 1 and "".join(parts) == text
