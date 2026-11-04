"""Translator interface."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Callable

Log = Callable[[str], None]

# Source languages offered in the UI, with NLLB-200 codes.
LANGUAGES = {
    "auto": ("تلقائي", None),
    "ja": ("ياباني", "jpn_Jpan"),
    "en": ("إنجليزي", "eng_Latn"),
    "zh": ("صيني", "zho_Hans"),
    "ko": ("كوري", "kor_Hang"),
    "ru": ("روسي", "rus_Cyrl"),
    "es": ("إسباني", "spa_Latn"),
    "fr": ("فرنسي", "fra_Latn"),
    "de": ("ألماني", "deu_Latn"),
}

_KANA = re.compile(r"[぀-ヿｦ-ﾟ]")
_HAN = re.compile(r"[一-鿿]")
_HANGUL = re.compile(r"[가-힯]")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")


def guess_language(text: str) -> str:
    if _KANA.search(text):
        return "ja"
    if _HANGUL.search(text):
        return "ko"
    if _HAN.search(text):
        return "ja" if len(text) < 6 else "zh"
    if _CYRILLIC.search(text):
        return "ru"
    return "en"


class TranslatorError(Exception):
    pass


class Translator(ABC):
    id = "?"
    name = "?"
    #: True if nothing leaves the computer.
    offline = False
    #: True if the engine reliably copies ⟦0⟧ style placeholders.
    keeps_placeholders = False
    #: Lines per request/batch.
    batch_size = 32

    def __init__(self, log: Log | None = None):
        self.log = log or (lambda m: None)
        self.cancelled = False

    def ready(self) -> tuple[bool, str]:
        """(usable, message). For example a model that is not downloaded yet."""
        return True, ""

    @abstractmethod
    def translate(self, texts: list[str], source: str, target: str = "ar") -> list[str]:
        """Translate a batch. ``source`` is a key of LANGUAGES (never 'auto')."""

    def close(self) -> None:
        pass
