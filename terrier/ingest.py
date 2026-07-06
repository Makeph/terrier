"""Ingest Claude Code session transcripts and markdown notes."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from . import db

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"

# Transcript lines worth indexing. Everything else (progress events,
# queue operations, tool results) is machinery, not memory.
_KEEP_TYPES = {"user", "assistant"}

# A single transcript message can be huge (file dumps, tool payloads).
# Cap what we index; search snippets never need more than this.
_MAX_BODY = 20_000
_MIN_BODY = 3


@dataclass
class IngestStats:
    scanned: int = 0
    ingested: int = 0
    skipped: int = 0
    docs: int = 0
    errors: list[str] = field(default_factory=list)


def decode_project_dir(name: str) -> str:
    """Turn Claude's mangled dir name (C--au2-Au2qwen) into something readable."""
    if name.startswith(("C--", "D--", "E--")):
        drive, rest = name[0], name[3:]
        return f"{drive}:/{rest.replace('--', '/.').replace('-', '/')}"
    return name


def _text_of(content) -> str:
    """Extract human text from a message content field (str or block list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return ""


def _parse_transcript(path: Path):
    """Yield (session_id, role, ts, text) for indexable lines of a .jsonl transcript."""
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("type") not in _KEEP_TYPES:
                continue
            msg = rec.get("message") or {}
            text = _text_of(msg.get("content")).strip()
            if len(text) < _MIN_BODY:
                continue
            # Skip harness noise: injected reminders, tool-result-only user turns.
            if text.startswith("<system-reminder>") or text.startswith("<local-command"):
                continue
            yield (
                rec.get("sessionId"),
                rec.get("type"),
                rec.get("timestamp"),
                text[:_MAX_BODY],
            )


def ingest_sessions(
    con: sqlite3.Connection,
    projects_dir: Path = CLAUDE_PROJECTS,
    stats: IngestStats | None = None,
) -> IngestStats:
    stats = stats or IngestStats()
    if not projects_dir.is_dir():
        return stats
    for transcript in sorted(projects_dir.glob("*/*.jsonl")):
        stats.scanned += 1
        try:
            st = transcript.stat()
            spath = str(transcript)
            if db.source_is_current(con, spath, st.st_mtime, st.st_size):
                stats.skipped += 1
                continue
            project = decode_project_dir(transcript.parent.name)
            source_id = db.replace_source(
                con, spath, "session", project, st.st_mtime, st.st_size
            )
            title = None
            for session_id, role, ts, text in _parse_transcript(transcript):
                if title is None and role == "user":
                    title = text.splitlines()[0][:120]
                db.add_doc(con, source_id, session_id, role, ts, title, text, project)
                stats.docs += 1
            stats.ingested += 1
        except OSError as exc:
            stats.errors.append(f"{transcript}: {exc}")
    con.commit()
    return stats


def ingest_notes(
    con: sqlite3.Connection,
    note_dirs: list[Path],
    stats: IngestStats | None = None,
) -> IngestStats:
    stats = stats or IngestStats()
    for root in note_dirs:
        root = root.expanduser().resolve()
        if not root.is_dir():
            stats.errors.append(f"{root}: not a directory")
            continue
        for md in sorted(root.rglob("*.md")):
            if any(part.startswith(".") or part == "node_modules" for part in md.parts):
                continue
            stats.scanned += 1
            try:
                st = md.stat()
                spath = str(md)
                if db.source_is_current(con, spath, st.st_mtime, st.st_size):
                    stats.skipped += 1
                    continue
                body = md.read_text(encoding="utf-8", errors="replace")[:_MAX_BODY]
                if len(body.strip()) < _MIN_BODY:
                    continue
                source_id = db.replace_source(
                    con, spath, "note", root.name, st.st_mtime, st.st_size
                )
                rel = str(md.relative_to(root))
                db.add_doc(con, source_id, None, "note", None, rel, body, root.name)
                stats.docs += 1
                stats.ingested += 1
            except OSError as exc:
                stats.errors.append(f"{md}: {exc}")
    con.commit()
    return stats


def remembered_note_dirs(con: sqlite3.Connection) -> list[Path]:
    raw = db.get_meta(con, "note_dirs")
    return [Path(p) for p in json.loads(raw)] if raw else []


def remember_note_dirs(con: sqlite3.Connection, dirs: list[Path]) -> None:
    merged = {str(p.expanduser().resolve()) for p in remembered_note_dirs(con)}
    merged.update(str(p.expanduser().resolve()) for p in dirs)
    db.set_meta(con, "note_dirs", json.dumps(sorted(merged)))
    con.commit()


def run(
    con: sqlite3.Connection,
    note_dirs: list[Path] | None = None,
    projects_dir: Path | None = None,
) -> IngestStats:
    """Full incremental ingest: sessions + all remembered note dirs."""
    stats = IngestStats()
    ingest_sessions(con, projects_dir or CLAUDE_PROJECTS, stats)
    if note_dirs:
        remember_note_dirs(con, note_dirs)
    ingest_notes(con, remembered_note_dirs(con), stats)
    db.set_meta(con, "last_ingest", _now_iso())
    con.commit()
    return stats


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")
