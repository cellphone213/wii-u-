"""A translation project: every translatable line of one game and its Arabic text.

Stored as a small SQLite file in the app's data folder (never in the game
folder). In private mode the database lives only in memory and disappears
when the app closes.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..engines.base import Entry
from .paths import projects_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY,
    channel TEXT NOT NULL,
    original TEXT NOT NULL,
    kind TEXT NOT NULL,
    speaker TEXT DEFAULT '',
    file TEXT DEFAULT '',
    line INTEGER DEFAULT 0,
    count INTEGER DEFAULT 1,
    ord INTEGER DEFAULT 0,
    translation TEXT DEFAULT '',
    status TEXT DEFAULT 'new',   -- new | machine | edited | locked | error
    problem TEXT DEFAULT '',
    engine TEXT DEFAULT '',
    updated REAL DEFAULT 0,
    UNIQUE(channel, original)
);
CREATE INDEX IF NOT EXISTS entries_status ON entries(status);
CREATE TABLE IF NOT EXISTS glossary (source TEXT PRIMARY KEY, target TEXT NOT NULL);
"""

STATUSES = ("new", "machine", "edited", "locked", "error")


@dataclass
class Row:
    id: int
    channel: str
    original: str
    kind: str
    speaker: str
    file: str
    line: int
    count: int
    ord: int
    translation: str
    status: str
    problem: str
    engine: str


def project_path_for(game_root: Path) -> Path:
    key = hashlib.sha1(str(Path(game_root).resolve()).lower().encode("utf-8")).hexdigest()[:16]
    return projects_dir() / f"{Path(game_root).name[:40]}-{key}.vnap"


class Project:
    def __init__(self, game_root: Path, private: bool = False, path: Path | None = None):
        self.game_root = Path(game_root)
        self.private = private
        self.path = None if private else (path or project_path_for(self.game_root))
        self.db = sqlite3.connect(":memory:" if private else str(self.path), check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.set_meta("game_root", str(self.game_root))
        self.db.commit()

    # -- meta ---------------------------------------------------------------
    def set_meta(self, key: str, value) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, json.dumps(value)))

    def get_meta(self, key: str, default=None):
        r = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    @property
    def markup(self) -> str:
        """Which engine markup the text uses (see core.protect.SYNTAXES)."""
        if not hasattr(self, "_markup"):
            self._markup = self.get_meta("markup", "renpy")
        return self._markup

    @markup.setter
    def markup(self, value: str) -> None:
        self._markup = value
        self.set_meta("markup", value)
        self.db.commit()

    # -- entries ------------------------------------------------------------
    def merge_entries(self, entries: Iterable[Entry]) -> tuple[int, int]:
        """Add newly extracted strings; keep existing translations. Returns (added, total)."""
        added = 0
        cur = self.db.cursor()
        for e in entries:
            status = "locked" if e.skip_reason else "new"
            r = cur.execute(
                "INSERT OR IGNORE INTO entries(channel, original, kind, speaker, file, line, count, ord, status, problem)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (e.channel, e.original, e.kind, e.speaker, e.file, e.line, e.count, e.order, status, e.skip_reason or ""),
            )
            if r.rowcount:
                added += 1
            else:
                cur.execute(
                    "UPDATE entries SET kind=?, speaker=?, file=?, line=?, count=?, ord=? WHERE channel=? AND original=?",
                    (e.kind, e.speaker, e.file, e.line, e.count, e.order, e.channel, e.original),
                )
        self.db.commit()
        total = self.db.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        return added, total

    def rows(self, where: str = "", params: tuple = ()) -> list[Row]:
        sql = "SELECT id, channel, original, kind, speaker, file, line, count, ord, translation, status, problem, engine FROM entries"
        if where:
            sql += " WHERE " + where
        sql += " ORDER BY ord, id"
        return [Row(*r) for r in self.db.execute(sql, params)]

    def pending(self, retranslate_machine: bool = False) -> list[Row]:
        statuses = ("new", "error", "machine") if retranslate_machine else ("new", "error")
        q = ",".join("?" * len(statuses))
        return self.rows(f"status IN ({q})", statuses)

    def set_translation(self, row_id: int, text: str, status: str, problem: str = "", engine: str = "") -> None:
        self.db.execute(
            "UPDATE entries SET translation=?, status=?, problem=?, engine=?, updated=? WHERE id=?",
            (text, status, problem, engine, time.time(), row_id),
        )

    def set_status(self, row_id: int, status: str) -> None:
        self.db.execute("UPDATE entries SET status=? WHERE id=?", (status, row_id))

    def commit(self) -> None:
        self.db.commit()

    def stats(self) -> dict[str, int]:
        out = {s: 0 for s in STATUSES}
        for s, n in self.db.execute("SELECT status, COUNT(*) FROM entries GROUP BY status"):
            out[s] = n
        out["total"] = sum(out.values())
        return out

    def translations(self) -> dict[tuple[str, str], str]:
        """Finished translations keyed by (channel, original), ready for the patch writer."""
        out: dict[tuple[str, str], str] = {}
        for ch, orig, tr in self.db.execute(
            "SELECT channel, original, translation FROM entries WHERE status IN ('machine','edited') AND translation != ''"
        ):
            out[(ch, orig)] = tr
        return out

    # -- glossary (character names and fixed terms) -------------------------
    def glossary(self) -> dict[str, str]:
        return dict(self.db.execute("SELECT source, target FROM glossary"))

    def set_glossary(self, source: str, target: str) -> None:
        if target.strip():
            self.db.execute("INSERT OR REPLACE INTO glossary VALUES (?, ?)", (source, target))
        else:
            self.db.execute("DELETE FROM glossary WHERE source=?", (source,))
        self.db.commit()

    # -- import/export for sharing or hand editing ---------------------------
    def export_json(self, path: Path) -> None:
        data = [
            {"channel": r.channel, "kind": r.kind, "speaker": r.speaker, "original": r.original,
             "translation": r.translation, "status": r.status}
            for r in self.rows()
        ]
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")

    def import_json(self, path: Path) -> int:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        n = 0
        for d in data:
            if not d.get("translation"):
                continue
            r = self.db.execute(
                "UPDATE entries SET translation=?, status='edited', problem='', updated=? WHERE channel=? AND original=?",
                (d["translation"], time.time(), d["channel"], d["original"]),
            )
            n += r.rowcount
        self.db.commit()
        return n

    def wipe(self) -> None:
        """Delete everything this project stored (used by the 'forget' button)."""
        self.db.close()
        if self.path and self.path.exists():
            self.path.unlink()

    def close(self) -> None:
        self.db.commit()
        self.db.close()
