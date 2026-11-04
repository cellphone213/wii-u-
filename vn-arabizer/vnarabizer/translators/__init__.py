from .base import LANGUAGES, Translator, TranslatorError, guess_language
from .deepl import DeepLTranslator
from .google_free import GoogleFreeTranslator
from .nllb import NLLBTranslator

TRANSLATORS = {
    NLLBTranslator.id: NLLBTranslator,
    GoogleFreeTranslator.id: GoogleFreeTranslator,
    DeepLTranslator.id: DeepLTranslator,
}

__all__ = ["LANGUAGES", "TRANSLATORS", "Translator", "TranslatorError", "guess_language"]
