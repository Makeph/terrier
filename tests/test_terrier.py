import json

import pytest

from terrier import db, graph, hook, ingest, search


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DEFAULT_HOME", tmp_path / ".terrier")
    return tmp_path


def make_transcript(projects_dir, project, session, lines):
    d = projects_dir / project
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{session}.jsonl"
    f.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    return f


def sample_lines(session="abc123"):
    return [
        {"type": "queue-operation", "operation": "enqueue", "content": "noise"},
        {
            "type": "user", "sessionId": session,
            "timestamp": "2026-07-01T10:00:00Z",
            "message": {"content": "fix the memory leak in the gateway"},
        },
        {
            "type": "assistant", "sessionId": session,
            "timestamp": "2026-07-01T10:01:00Z",
            "message": {"content": [
                {"type": "text", "text": "The leak came from the connection pool."},
                {"type": "tool_use", "name": "Bash", "input": {}},
            ]},
        },
        {
            "type": "user", "sessionId": session,
            "timestamp": "2026-07-01T10:02:00Z",
            "message": {"content": "<system-reminder>injected</system-reminder>"},
        },
    ]


def test_ingest_and_search(home, tmp_path):
    projects = tmp_path / "projects"
    make_transcript(projects, "C--repo-alpha", "abc123", sample_lines())
    con = db.connect()
    stats = ingest.ingest_sessions(con, projects)
    assert stats.ingested == 1
    assert stats.docs == 2  # queue-op and system-reminder filtered out

    hits = search.search(con, "memory leak")
    assert hits and hits[0].session_id == "abc123"
    assert "[leak]" in hits[0].snippet or "[memory]" in hits[0].snippet
    assert hits[0].title == "fix the memory leak in the gateway"

    # incremental: unchanged file is skipped
    stats2 = ingest.ingest_sessions(con, projects)
    assert stats2.skipped == 1 and stats2.ingested == 0


def test_reingest_replaces_docs(home, tmp_path):
    projects = tmp_path / "projects"
    f = make_transcript(projects, "C--repo-alpha", "abc123", sample_lines())
    con = db.connect()
    ingest.ingest_sessions(con, projects)
    # file grows -> re-ingested without duplicating docs
    f.write_text(f.read_text(encoding="utf-8") + "\n" + json.dumps(
        {"type": "user", "sessionId": "abc123",
         "timestamp": "2026-07-01T11:00:00Z",
         "message": {"content": "second question about the gateway"}}
    ), encoding="utf-8")
    ingest.ingest_sessions(con, projects)
    n = con.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    assert n == 3


def test_notes_and_recap(home, tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "adr-001.md").write_text("# ADR 1\nWe chose sqlite fts5.", encoding="utf-8")
    projects = tmp_path / "projects"
    make_transcript(projects, "C--repo-alpha", "abc123", sample_lines())
    con = db.connect()
    ingest.run(con, note_dirs=[notes], projects_dir=projects)

    assert search.search(con, "fts5")[0].role == "note"
    groups = search.recap(con, days=10_000)
    assert groups[0]["sessions"][0]["turns"] == 2
    st = search.status(con)
    assert st["sessions"] == 1 and st["notes"] == 1


def test_query_quoting():
    assert search._fts_quote("hello world") == '"hello" "world"'
    assert search._fts_quote('"exact phrase"') == '"exact phrase"'
    assert search._fts_quote("a OR b") == "a OR b"


def test_graph_html(home, tmp_path):
    projects = tmp_path / "projects"
    make_transcript(projects, "C--repo-alpha", "abc123", sample_lines())
    con = db.connect()
    ingest.ingest_sessions(con, projects)
    html = graph.render_html(con)
    assert "__DATA__" not in html and "TERRIER" in html
    g = graph.build_graph(con)
    kinds = {n["kind"] for n in g["nodes"]}
    assert {"project", "session"} <= kinds


def test_hook_roundtrip(tmp_path, monkeypatch):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"model": "opus"}), encoding="utf-8")
    monkeypatch.setattr(hook, "SETTINGS", settings)
    assert hook.install() is True
    assert hook.install() is False
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["model"] == "opus"
    assert hook.remove() is True
    assert "SessionEnd" not in json.loads(settings.read_text(encoding="utf-8")).get("hooks", {})


def test_decode_project_dir():
    assert ingest.decode_project_dir("C--au2-Au2qwen") == "C:/au2/Au2qwen"
