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


def _suspicious(original: str, translated: str, syntax: str = "renpy") -> str:
    """Cheap checks for typical machine-translation failures."""
    plain = protect._strip_markup(translated, syntax)
    if not _ARABIC.search(plain) and re.search(r"[A-Za-z぀-ヿ一-鿿]", plain):
        return "no Arabic in output"
    if len(plain) > 6 * max(8, len(protect._strip_markup(original, syntax))):
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
    syntax = project.markup
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
        m = _apply_glossary(protect.mask(r.original, syntax), glossary)
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

    # 2) Lines with markup inside the sentence. Names and numbers become plain
    #    numbers so the sentence is translated whole; formatting codes split
    #    it into pieces. If the translator loses a number, fall back to
    #    translating every piece between markup separately.
    if pieces:
        failed = _translate_pieces(pieces, lang, translator, project, cache, stats, sentinels=True)
        if failed:
            _translate_pieces(failed, lang, translator, project, cache, stats, sentinels=False)


def _translate_pieces(items, lang, translator: Translator, project: Project, cache, stats: RunStats, sentinels: bool):
    prepared = []
    for r, m in items:
        body, smap = protect.sentinelize(m) if sentinels else (m.body, {})
        parts = protect._PLACEHOLDER_RE.split(body)  # even: prose, odd: placeholder index
        prepared.append((r, m, parts, smap))

    cores = [protect.split_edges(p.strip())[1] for _, _, parts, _ in prepared for p in parts[0::2] if p.strip()]
    todo = list(dict.fromkeys(c for c in cores if c and (lang, c) not in cache))
    if todo:
        for src, dst in zip(todo, translator.translate(todo, lang)):
            cache[(lang, src)] = dst

    failed = []
    for r, m, parts, smap in prepared:
        out = []
        last = len(parts) - 1
        for k, part in enumerate(parts):
            if k % 2:
                out.append(protect._PLACEHOLDER.format(part))
                continue
            if not part.strip():
                out.append(part)
                continue
            text = part.strip()
            lead_ws = part[: len(part) - len(part.lstrip())]
            trail_ws = part[len(part.rstrip()) :]
            lead, core, trail = protect.split_edges(text)
            tr = cache.get((lang, core), core) if core else ""
            # Japanese/Chinese put no spaces around names and codes; Arabic needs them.
            if not lead_ws and k > 0 and not lead and protect.is_cjk(text[0]):
                lead_ws = " "
            if not trail_ws and k < last and protect.is_cjk(text[-1]):
                trail_ws = " "
            out.append(lead_ws + protect.arabic_punct(lead) + tr + protect.arabic_punct(trail) + trail_ws)
        body = protect.desentinelize("".join(out), smap)
        if body is None:
            failed.append((r, m))
            continue
        try:
            final = protect.unmask(m, body)
        except protect.RestoreError:
            failed.append((r, m))
            continue
        _store(project, r, final, translator, stats)
    return failed


def _store(project: Project, r: Row, final: str, translator: Translator, stats: RunStats) -> None:
    syntax = project.markup
    problems = protect.validate(r.original, final, syntax)
    if problems:
        project.set_translation(r.id, final, "error", "; ".join(problems), translator.id)
        stats.errors += 1
        return
    warn = _suspicious(r.original, final, syntax)
    project.set_translation(r.id, final, "machine", warn, translator.id)
    stats.translated += 1
