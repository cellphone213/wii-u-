"""DeepL official API (needs your own API key; Free or Pro)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .base import Translator, TranslatorError
from .download import USER_AGENT, _context

DEEPL_SOURCE = {"ja": "JA", "en": "EN", "zh": "ZH", "ko": "KO", "ru": "RU", "es": "ES", "fr": "FR", "de": "DE"}


class DeepLTranslator(Translator):
    id = "deepl"
    name = "DeepL API (مفتاح خاص بك)"
    offline = False
    keeps_placeholders = True
    batch_size = 50

    def __init__(self, api_key: str = "", log=None):
        super().__init__(log)
        self.api_key = api_key.strip()

    def ready(self):
        if not self.api_key:
            return False, "ضع مفتاح DeepL في الإعدادات."
        return True, ""

    @property
    def url(self) -> str:
        host = "api-free.deepl.com" if self.api_key.endswith(":fx") else "api.deepl.com"
        return f"https://{host}/v2/translate"

    def translate(self, texts: list[str], source: str, target: str = "ar") -> list[str]:
        fields = [("text", t) for t in texts] + [("target_lang", "AR")]
        if source in DEEPL_SOURCE:
            fields.append(("source_lang", DEEPL_SOURCE[source]))
        body = urllib.parse.urlencode(fields).encode()
        backoff = 5.0
        while True:
            req = urllib.request.Request(
                self.url, data=body, headers={"Authorization": f"DeepL-Auth-Key {self.api_key}", "User-Agent": USER_AGENT}
            )
            try:
                with urllib.request.urlopen(req, timeout=60, context=_context()) as r:
                    data = json.loads(r.read().decode("utf-8"))
                return [t["text"] for t in data["translations"]]
            except urllib.error.HTTPError as e:
                if e.code == 429 and backoff < 300 and not self.cancelled:
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                if e.code == 456:
                    raise TranslatorError("DeepL: انتهت حصة الأحرف في حسابك") from e
                if e.code == 403:
                    raise TranslatorError("DeepL: المفتاح غير صحيح") from e
                raise TranslatorError(f"DeepL error HTTP {e.code}") from e
            except urllib.error.URLError as e:
                raise TranslatorError(f"no connection to DeepL: {e.reason}") from e
