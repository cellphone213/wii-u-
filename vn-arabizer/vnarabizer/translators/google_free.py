"""Google Translate's free web endpoint, used politely.

Like Translator++, this keeps the number of requests low so a single user
is not blocked: many lines go in one request, there is a pause between
requests, repeated lines are never re-sent, and when Google answers
"too many requests" the app waits and then continues where it stopped.
It does not rotate IP addresses or otherwise try to get around Google's
limits.

Privacy: Google sees the text and your IP address. Use the offline
translator for full privacy.
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request

from .base import Translator, TranslatorError
from .download import USER_AGENT, _context

URL = "https://translate.googleapis.com/translate_a/single"
MAX_CHARS = 4000  # per request
SEPARATOR = "\n"


class GoogleFreeTranslator(Translator):
    id = "google"
    name = "Google (مجاني، يحتاج إنترنت)"
    offline = False
    keeps_placeholders = True
    batch_size = 40

    def __init__(self, min_interval: float = 1.5, log=None):
        super().__init__(log)
        self.min_interval = min_interval
        self._last = 0.0

    def _request(self, text: str, source: str, target: str) -> str:
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait + random.uniform(0, 0.3))
        backoff = 30.0
        while True:
            if self.cancelled:
                raise TranslatorError("cancelled")
            body = urllib.parse.urlencode({"q": text}).encode()
            params = urllib.parse.urlencode({"client": "gtx", "sl": source, "tl": target, "dt": "t"})
            req = urllib.request.Request(
                f"{URL}?{params}",
                data=body,
                headers={"User-Agent": USER_AGENT, "Content-Type": "application/x-www-form-urlencoded;charset=utf-8"},
            )
            try:
                with urllib.request.urlopen(req, timeout=30, context=_context()) as r:
                    data = json.loads(r.read().decode("utf-8"))
                self._last = time.monotonic()
                return "".join(seg[0] for seg in data[0] if seg and seg[0])
            except urllib.error.HTTPError as e:
                if e.code in (429, 503):
                    self.log(f"Google asked to slow down; waiting {int(backoff)} s and continuing")
                    end = time.monotonic() + backoff
                    while time.monotonic() < end:
                        if self.cancelled:
                            raise TranslatorError("cancelled")
                        time.sleep(0.5)
                    backoff = min(backoff * 2, 600)
                    continue
                raise TranslatorError(f"Google error HTTP {e.code}") from e
            except urllib.error.URLError as e:
                raise TranslatorError(f"no connection to Google: {e.reason}") from e

    def translate(self, texts: list[str], source: str, target: str = "ar") -> list[str]:
        out: list[str] = []
        group: list[str] = []

        def flush():
            if not group:
                return
            joined = SEPARATOR.join(t.replace("\n", " ") for t in group)
            result = self._request(joined, source, target).split(SEPARATOR)
            if len(result) != len(group):
                # Lines merged or split: redo this group one line at a time.
                result = [self._request(t, source, target) for t in group]
            out.extend(r.strip() for r in result)
            group.clear()

        size = 0
        for t in texts:
            if group and size + len(t) + 1 > MAX_CHARS:
                flush()
                size = 0
            group.append(t)
            size += len(t) + 1
        flush()
        return out
