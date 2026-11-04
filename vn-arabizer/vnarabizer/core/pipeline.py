"""Translate a project's pending lines safely.

For every line: protect markup -> translate the prose -> put markup back ->
validate. A line that fails validation is marked as an error and is never
written into the game.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable

from ..translators.base import Translator, TranslatorError, guess_language
from . import protect
from .project import Project, Row

Progress = Callable[[int, int, str], None]  # (done, total, message)

_ARABIC = re.compile(r"[؀-ۿﭐ-﷿ﹰ-﻿]")


@dataclass
class RunStats:
    translated: int = 0
    errors: int = 0
    skipped: int = 0
    seconds: float = 0.0


def _apply_glossary(m: protect.Masked, glossary: dict[str, str]) -> protect.Masked:
    """Lock glossary terms (character names etc.) to their fixed Arabic form."""
    if not glossary:
        return m
    body = m.body
    for src in sorted(glossary, key=len, reverse=True):
        if not src or src not in body:
            continue
        pattern = re.compile(r"(?<![\w])" + re.escape(src) + r"(?![\w])") if src.isascii() else re.compile(re.escape(src))
        if not pattern.search(body):
            continue
        idx = len(m.tokens)
        m.tokens.append(glossary[src])
        body = pattern.sub(protect._PLACEHOLDER.format(idx), body)
    m.body = body
    return m


def _suspicious(original: str, translated: str) -> str:
    """Cheap checks for typical machine-translation failures."""
    plain = protect._strip_markup(translated)
    if not _ARABIC.search(plain) and re.search(r"[A-Za-z぀-ヿ一-鿿]", plain):
        return "no Arabic in output"
    if len(plain) > 6 * max(8, len(protect._strip_markup(original))):
        return "output much longer than input"
    if re.search(r"(.{3,})\1{3,}", plain):
        return "repeated words"
    return ""


def run(
    project: Project,
    translator: Translator,
    source: str = "auto",
    rows: list[Row] | None = None,
    progress: Progress | None = None,
    cancel: Callable[[], bool] = lambda: False,
) -> RunStats:
    stats = RunStats()
    start = time.monotonic()
    rows = rows if rows is not None else project.pending()
    rows = [r for r in rows if r.status != "locked"]
    glossary = project.glossary()
    total = len(rows)
    done = 0

    # Glossary entries translate themselves (character names).
    work: list[tuple[Row, protect.Masked]] = []
    for r in rows:
        if r.original in glossary:
            project.set_translation(r.id, glossary[r.original], "edited", "", "glossary")
            stats.translated += 1
            done += 1
            continue
        m = _apply_glossary(protect.mask(r.original), glossary)
        if not m.needs_translation:
            project.set_translation(r.id, "", "locked", "nothing to translate", "")
            stats.skipped += 1
            done += 1
            continue
        work.append((r, m))
    project.commit()

    # Group by source language so each batch has one language.
    def lang_of(text: str) -> str:
        return guess_language(text) if source == "auto" else source

    cache: dict[tuple[str, str], str] = {}
    size = translator.batch_size
    i = 0
    while i < len(work):
        if cancel():
            translator.cancelled = True
            break
        batch = work[i : i + size]
        i += size
        by_lang: dict[str, list[tuple[Row, protect.Masked]]] = {}
        for r, m in batch:
            by_lang.setdefault(lang_of(r.original), []).append((r, m))
        for lang, items in by_lang.items():
            try:
                _translate_items(items, lang, translator, project, cache, stats)
            except TranslatorError as e:
                for r, _ in items:
                    project.set_translation(r.id, "", "error", str(e), translator.id)
                    stats.errors += 1
                if "cancelled" in str(e):
                    break
        project.commit()
        done += len(batch)
        if progress:
            progress(done, total, f"{done}/{total}")
    stats.seconds = time.monotonic() - start
    return stats


def _translate_items(items, lang, translator: Translator, project: Project, cache, stats: RunStats) -> None:
    # 1) Lines whose prose has no placeholders, or engines that keep placeholders:
    #    translate the whole body in one go.
    whole = [(r, m) for r, m in items if not m.tokens or translator.keeps_placeholders]
    pieces = [(r, m) for r, m in items if m.tokens and not translator.keeps_placeholders]

    texts = list(dict.fromkeys(m.body for _, m in whole if (lang, m.body) not in cache))
    if texts:
        for src, dst in zip(texts, translator.translate(texts, lang)):
            cache[(lang, src)] = dst
    for r, m in whole:
        out = cache[(lang, m.body)]
        try:
            final = protect.unmask(m, out)
        except protect.RestoreError:
            pieces.append((r, m))  # placeholders lost: retry piece by piece
            continue
        _store(project, r, final, translator, stats)

    # 2) Piece-by-piece: translate prose between markup separately so every
    #    tag and variable stays exactly where it was.
    if pieces:
        cores = [protect.split_edges(s.strip())[1] for _, m in pieces for s in protect.segments(m) if s.strip()]
        segs = list(dict.fromkeys(c for c in cores if c and (lang, c) not in cache))
        if segs:
            for src, dst in zip(segs, translator.translate(segs, lang)):
                cache[(lang, src)] = dst
        for r, m in pieces:
            translated = []
            for s in protect.segments(m):
                if not s.strip():
                    translated.append(s)
                    continue
                lead, core, trail = protect.split_edges(s.strip())
                tr = cache.get((lang, core), core) if core else ""
                translated.append(protect.arabic_punct(lead) + tr + protect.arabic_punct(trail))
            _store(project, r, protect.join_segments(m, translated), translator, stats)


def _store(project: Project, r: Row, final: str, translator: Translator, stats: RunStats) -> None:
    problems = protect.validate(r.original, final)
    if problems:
        project.set_translation(r.id, final, "error", "; ".join(problems), translator.id)
        stats.errors += 1
        return
    warn = _suspicious(r.original, final)
    project.set_translation(r.id, final, "machine", warn, translator.id)
    stats.translated += 1
