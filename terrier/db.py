"""SQLite storage layer. One file, FTS5 for search, nothing else."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DEFAULT_HOME = Path(os.environ.get("TERRIER_HOME", Path.home() / ".terrier"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id      INTEGER PRIMARY KEY,
    path    TEXT NOT NULL UNIQUE,   -- absolute path of the ingested file
    kind    TEXT NOT NULL,          -- 'session' | 'note'
    project TEXT NOT NULL,          -- decoded project dir or note root name
    mtime   REAL NOT NULL,
    size    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS docs (
    id         INTEGER PRIMARY KEY,
    source_id  INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    session_id TEXT,                -- session uuid for transcripts, NULL for notes
    role       TEXT NOT NULL,       -- 'user' | 'assistant' | 'note'
    ts         TEXT,                -- ISO timestamp when known
    title      TEXT,                -- first user prompt (sessions) or filename (notes)
    body       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS docs_source ON docs(source_id);

CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
    body, title, project UNINDEXED, role UNINDEXED,
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def db_path(home: Path | None = None) -> Path:
    return (home or DEFAULT_HOME) / "terrier.db"


def connect(home: Path | None = None) -> sqlite3.Connection:
    path = db_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA)
    return con


def replace_source(
    con: sqlite3.Connection,
    path: str,
    kind: str,
    project: str,
    mtime: float,
    size: int,
) -> int:
    """Insert or refresh a source row, dropping any docs it previously owned."""
    row = con.execute("SELECT id FROM sources WHERE path = ?", (path,)).fetchone()
    if row:
        source_id = row["id"]
        doc_ids = [r["id"] for r in con.execute(
            "SELECT id FROM docs WHERE source_id = ?", (source_id,))]
        if doc_ids:
            qs = ",".join("?" * len(doc_ids))
            con.execute(f"DELETE FROM docs_fts WHERE rowid IN ({qs})", doc_ids)
            con.execute(f"DELETE FROM docs WHERE id IN ({qs})", doc_ids)
        con.execute(
            "UPDATE sources SET mtime = ?, size = ?, project = ? WHERE id = ?",
            (mtime, size, project, source_id),
        )
        return source_id
    cur = con.execute(
        "INSERT INTO sources (path, kind, project, mtime, size) VALUES (?, ?, ?, ?, ?)",
        (path, kind, project, mtime, size),
    )
    return cur.lastrowid


def source_is_current(con: sqlite3.Connection, path: str, mtime: float, size: int) -> bool:
    row = con.execute(
        "SELECT mtime, size FROM sources WHERE path = ?", (path,)
    ).fetchone()
    return bool(row) and row["mtime"] == mtime and row["size"] == size


def add_doc(
    con: sqlite3.Connection,
    source_id: int,
    session_id: str | None,
    role: str,
    ts: str | None,
    title: str | None,
    body: str,
    project: str,
) -> None:
    cur = con.execute(
        "INSERT INTO docs (source_id, session_id, role, ts, title, body)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (source_id, session_id, role, ts, title, body),
    )
    con.execute(
        "INSERT INTO docs_fts (rowid, body, title, project, role)"
        " VALUES (?, ?, ?, ?, ?)",
        (cur.lastrowid, body, title or "", project, role),
    )


def set_meta(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def get_meta(con: sqlite3.Connection, key: str) -> str | None:
    row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None
