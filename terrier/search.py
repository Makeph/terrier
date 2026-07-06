"""Full-text search with citations, and the recap digest."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass
class Hit:
    snippet: str
    title: str
    project: str
    role: str
    session_id: str | None
    ts: str | None
    path: str
    score: float


def _fts_quote(query: str) -> str:
    """Treat the query as plain words, not FTS5 syntax, unless it already
    uses quotes or operators on purpose."""
    specials = set('"*()')
    if specials & set(query) or " OR " in query or " NOT " in query:
        return query
    return " ".join(
        '"{}"'.format(tok.replace('"', '""')) for tok in query.split()
    )


def search(
    con: sqlite3.Connection,
    query: str,
    limit: int = 10,
    project: str | None = None,
    days: int | None = None,
) -> list[Hit]:
    sql = """
        SELECT snippet(docs_fts, 0, '[', ']', ' … ', 24) AS snip,
               d.title, d.session_id, d.role, d.ts,
               s.project, s.path,
               bm25(docs_fts) AS score
        FROM docs_fts
        JOIN docs d ON d.id = docs_fts.rowid
        JOIN sources s ON s.id = d.source_id
        WHERE docs_fts MATCH ?
    """
    args: list = [_fts_quote(query)]
    if project:
        sql += " AND s.project LIKE ?"
        args.append(f"%{project}%")
    if days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        sql += " AND (d.ts IS NULL OR d.ts >= ?)"
        args.append(cutoff)
    sql += " ORDER BY score LIMIT ?"
    args.append(limit)
    return [
        Hit(
            snippet=r["snip"],
            title=r["title"] or "",
            project=r["project"],
            role=r["role"],
            session_id=r["session_id"],
            ts=r["ts"],
            path=r["path"],
            score=r["score"],
        )
        for r in con.execute(sql, args)
    ]


def recap(con: sqlite3.Connection, days: int = 7) -> list[dict]:
    """What happened recently: sessions grouped by project, newest first."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = con.execute(
        """
        SELECT s.project, d.session_id,
               MIN(d.ts) AS started, MAX(d.ts) AS ended,
               COUNT(*) AS turns,
               MAX(d.title) AS title
        FROM docs d JOIN sources s ON s.id = d.source_id
        WHERE d.ts >= ? AND d.session_id IS NOT NULL
        GROUP BY s.project, d.session_id
        ORDER BY started DESC
        """,
        (cutoff,),
    ).fetchall()
    out: dict[str, list] = {}
    for r in rows:
        out.setdefault(r["project"], []).append(
            {
                "session_id": r["session_id"],
                "started": r["started"],
                "ended": r["ended"],
                "turns": r["turns"],
                "title": r["title"] or "(untitled session)",
            }
        )
    return [{"project": k, "sessions": v} for k, v in out.items()]


def status(con: sqlite3.Connection) -> dict:
    q = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "sessions": q("SELECT COUNT(*) FROM sources WHERE kind = 'session'"),
        "notes": q("SELECT COUNT(*) FROM sources WHERE kind = 'note'"),
        "docs": q("SELECT COUNT(*) FROM docs"),
        "projects": q("SELECT COUNT(DISTINCT project) FROM sources"),
    }
