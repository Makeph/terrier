"""terrier — dig through your agents' past. Stdlib only, on purpose."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, db, graph, hook, ingest, search


def _fmt_ts(ts: str | None) -> str:
    return ts[:16].replace("T", " ") if ts else "····-··-·· ··:··"


def cmd_ingest(args) -> int:
    con = db.connect()
    stats = ingest.run(con, note_dirs=[Path(p) for p in args.notes])
    if not args.quiet:
        print(
            f"scanned {stats.scanned} · new/updated {stats.ingested}"
            f" · unchanged {stats.skipped} · docs added {stats.docs}"
        )
        for err in stats.errors[:5]:
            print(f"  ! {err}", file=sys.stderr)
    return 0


def cmd_search(args) -> int:
    con = db.connect()
    hits = search.search(
        con, " ".join(args.query), limit=args.limit,
        project=args.project, days=args.days,
    )
    if not hits:
        print("nothing buried here — try `terrier ingest` first?")
        return 1
    for h in hits:
        where = h.project.rsplit("/", 1)[-1]
        sid = f" · {h.session_id[:8]}" if h.session_id else ""
        print(f"┌ {_fmt_ts(h.ts)} · {where}{sid} · {h.role}")
        if h.title:
            print(f"│ {h.title}")
        snippet = " ".join((h.snippet or "").split())
        print(f"└ {snippet}\n")
    return 0


def cmd_recap(args) -> int:
    con = db.connect()
    groups = search.recap(con, days=args.days)
    if not groups:
        print(f"no sessions in the last {args.days} days.")
        return 0
    print(f"── last {args.days} days ──")
    for g in groups:
        print(f"\n{g['project']}")
        for s in g["sessions"]:
            print(f"  {_fmt_ts(s['started'])}  ({s['turns']:>3} turns)  {s['title'][:90]}")
    return 0


def cmd_graph(args) -> int:
    con = db.connect()
    out = Path(args.output)
    out.write_text(graph.render_html(con), encoding="utf-8")
    print(f"graph written to {out.resolve()}")
    if args.open:
        import webbrowser

        webbrowser.open(out.resolve().as_uri())
    return 0


def cmd_status(args) -> int:
    con = db.connect()
    st = search.status(con)
    last = db.get_meta(con, "last_ingest") or "never"
    path = db.db_path()
    size = path.stat().st_size / 1e6 if path.exists() else 0
    print(f"burrow      {path}  ({size:.1f} MB)")
    print(f"last dig    {last}")
    print(f"sessions    {st['sessions']}")
    print(f"notes       {st['notes']}")
    print(f"documents   {st['docs']}")
    print(f"projects    {st['projects']}")
    return 0


def cmd_hook(args) -> int:
    if args.action == "install":
        changed = hook.install()
        print("hook installed — sessions now auto-ingest on end."
              if changed else "hook already installed.")
    else:
        changed = hook.remove()
        print("hook removed." if changed else "no terrier hook found.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="terrier",
        description="Local-first memory for your coding agents: index Claude Code"
        " sessions and markdown notes into one SQLite file, then dig.",
    )
    p.add_argument("--version", action="version", version=f"terrier {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("ingest", help="index new/changed sessions and notes")
    sp.add_argument("--notes", action="append", default=[], metavar="DIR",
                    help="markdown directory to index (remembered for next time)")
    sp.add_argument("--quiet", action="store_true", help="no output (for hooks)")
    sp.set_defaults(fn=cmd_ingest)

    sp = sub.add_parser("search", help="full-text search with citations")
    sp.add_argument("query", nargs="+")
    sp.add_argument("-n", "--limit", type=int, default=10)
    sp.add_argument("-p", "--project", help="filter by project substring")
    sp.add_argument("-d", "--days", type=int, help="only the last N days")
    sp.set_defaults(fn=cmd_search)

    sp = sub.add_parser("recap", help="what did I do lately?")
    sp.add_argument("-d", "--days", type=int, default=7)
    sp.set_defaults(fn=cmd_recap)

    sp = sub.add_parser("graph", help="export an interactive HTML knowledge graph")
    sp.add_argument("-o", "--output", default="terrier-graph.html")
    sp.add_argument("--open", action="store_true", help="open in browser")
    sp.set_defaults(fn=cmd_graph)

    sp = sub.add_parser("status", help="what's in the burrow")
    sp.set_defaults(fn=cmd_status)

    sp = sub.add_parser("hook", help="auto-ingest via Claude Code SessionEnd hook")
    sp.add_argument("action", choices=["install", "remove"])
    sp.set_defaults(fn=cmd_hook)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
