"""Offline translation with Meta's NLLB-200 through CTranslate2.

Runs entirely on this computer: no network, no logs, no limits.
Uses the NVIDIA GPU when available (RTX 20xx and newer), else the CPU.
"""

from __future__ import annotations

import re
import threading

from . import cuda, models
from .base import LANGUAGES, Translator, TranslatorError

TARGET = "arb_Arab"  # Modern Standard Arabic

# Sentence ends for splitting long lines (NLLB handles one sentence best).
_SENT_SPLIT = re.compile(r"(?<=[。！？!?…])\s*|(?<=[.])\s+(?=[A-Z\"'“])")


def _split_sentences(text: str, max_len: int = 220) -> list[str]:
    if len(text) <= max_len:
        return [text]
    parts = [p for p in _SENT_SPLIT.split(text) if p and p.strip()]
    return parts or [text]


class NLLBTranslator(Translator):
    id = "nllb"
    name = "NLLB-200 (بدون إنترنت)"
    offline = True
    keeps_placeholders = False
    batch_size = 32

    def __init__(self, model_key: str = models.DEFAULT_MODEL, device: str = "auto", beam_size: int = 4, log=None):
        super().__init__(log)
        self.model_key = model_key
        self.device_pref = device
        self.beam_size = beam_size
        self._translator = None
        self._sp = None
        self.device = None
        self._lock = threading.Lock()

    def ready(self) -> tuple[bool, str]:
        if not models.is_installed(self.model_key):
            return False, "النموذج غير منزّل بعد. اضغط زر «تنزيل النموذج»."
        return True, ""

    def _load(self) -> None:
        if self._translator is not None:
            return
        ok, msg = self.ready()
        if not ok:
            raise TranslatorError(msg)
        cuda.add_to_path()
        try:
            import ctranslate2
            import sentencepiece as spm
        except ImportError as e:  # pragma: no cover - packaging problem
            raise TranslatorError(f"missing component: {e}") from e

        path = str(models.model_path(self.model_key))
        self._sp = spm.SentencePieceProcessor(model_file=str(models.model_path(self.model_key) / models.TOKENIZER))

        attempts = []
        if self.device_pref in ("auto", "cuda"):
            try:
                if ctranslate2.get_cuda_device_count() > 0:
                    attempts += [("cuda", "int8_float16"), ("cuda", "float16")]
            except Exception:
                pass
        attempts += [("cpu", "int8"), ("cpu", "default")]
        last = None
        for device, ctype in attempts:
            try:
                t = ctranslate2.Translator(path, device=device, compute_type=ctype)
                # A tiny warm-up catches missing GPU libraries now rather than mid-run.
                t.translate_batch([["eng_Latn", "▁Hi", "</s>"]], target_prefix=[[TARGET]], max_decoding_length=4)
                self._translator, self.device = t, f"{device} ({ctype})"
                self.log(f"NLLB loaded on {self.device}")
                return
            except Exception as e:
                last = e
                self.log(f"NLLB: {device}/{ctype} not available: {e}")
        raise TranslatorError(f"could not load the model: {last}")

    def translate(self, texts: list[str], source: str, target: str = "ar") -> list[str]:
        if not texts:
            return []
        src_code = LANGUAGES.get(source, (None, None))[1]
        if not src_code:
            raise TranslatorError(f"unsupported source language {source}")
        with self._lock:
            self._load()
            # Split long lines into sentences, translate all pieces in one go.
            pieces: list[str] = []
            owners: list[int] = []
            for i, t in enumerate(texts):
                for s in _split_sentences(t):
                    pieces.append(s)
                    owners.append(i)
            batch = [[src_code] + self._sp.encode(p, out_type=str) + ["</s>"] for p in pieces]
            results = self._translator.translate_batch(
                batch,
                target_prefix=[[TARGET]] * len(batch),
                beam_size=self.beam_size,
                max_batch_size=self.batch_size,
                max_decoding_length=min(512, max(len(b) for b in batch) * 3 + 16),
                repetition_penalty=1.1,
                no_repeat_ngram_size=4,
            )
            out_pieces = []
            for r in results:
                toks = r.hypotheses[0]
                if toks and toks[0] == TARGET:
                    toks = toks[1:]
                out_pieces.append(self._sp.decode(toks).strip())
        # NLLB tends to wrap lone names in brackets: "Eileen" -> "(ايلين)".
        out_pieces = [
            p[1:-1].strip() if p.startswith("(") and p.endswith(")") and not src.lstrip().startswith("(") else p
            for p, src in zip(out_pieces, pieces)
        ]
        out = [""] * len(texts)
        for owner, piece in zip(owners, out_pieces):
            out[owner] = (out[owner] + " " + piece).strip()
        return out

    def close(self) -> None:
        self._translator = None
