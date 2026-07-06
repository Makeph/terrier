"""Install/remove the Claude Code hook that keeps the burrow fresh.

Adds a SessionEnd hook to ~/.claude/settings.json so every finished
session is re-indexed automatically — the same trick Stash uses to
stream transcripts, minus the cloud.
"""

from __future__ import annotations

import json
from pathlib import Path

SETTINGS = Path.home() / ".claude" / "settings.json"
HOOK_COMMAND = "terrier ingest --quiet"
_EVENT = "SessionEnd"


def _load() -> dict:
    if SETTINGS.is_file():
        return json.loads(SETTINGS.read_text(encoding="utf-8"))
    return {}


def _is_ours(entry: dict) -> bool:
    return any(
        h.get("command", "").startswith("terrier ingest")
        for h in entry.get("hooks", [])
    )


def install() -> bool:
    """Returns True if the hook was added, False if already present."""
    settings = _load()
    hooks = settings.setdefault("hooks", {})
    entries = hooks.setdefault(_EVENT, [])
    if any(_is_ours(e) for e in entries):
        return False
    entries.append(
        {"hooks": [{"type": "command", "command": HOOK_COMMAND, "timeout": 120}]}
    )
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return True


def remove() -> bool:
    """Returns True if a hook was removed."""
    settings = _load()
    entries = settings.get("hooks", {}).get(_EVENT, [])
    kept = [e for e in entries if not _is_ours(e)]
    if len(kept) == len(entries):
        return False
    settings["hooks"][_EVENT] = kept
    if not kept:
        del settings["hooks"][_EVENT]
    SETTINGS.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    return True
