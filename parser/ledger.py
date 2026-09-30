"""
Durable per-turn ledger (SQLite).

Claude Code deletes transcripts after `cleanupPeriodDays` (default 30), so totals built
only from ~/.claude/projects shrink over time. Every refresh upserts parsed sessions and
turns here; the dashboard is then built from the ledger, so history outlives transcripts.

Stored: token counts, timestamps, models, file paths touched, session names (already shown
in the UI). Never stored: prompt or response text.
"""

import json
import os
import pathlib
import sqlite3

try:
    from .models import SessionStats, recompute_totals
except ImportError:
    from models import SessionStats, recompute_totals

LEDGER_PATH = pathlib.Path(os.environ.get("LEDGER_PATH", "/data/ledger.sqlite"))
SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    parent_session_id TEXT NOT NULL DEFAULT '',
    is_subagent INTEGER NOT NULL DEFAULT 0,
    agent_type TEXT NOT NULL DEFAULT '',
    attribution_skill TEXT NOT NULL DEFAULT '',
    project_path TEXT NOT NULL DEFAULT '',
    session_name TEXT NOT NULL DEFAULT '',
    start_ts INTEGER NOT NULL DEFAULT 0,
    end_ts INTEGER NOT NULL DEFAULT 0,
    caveman INTEGER NOT NULL DEFAULT 0,
    caveman_mode TEXT NOT NULL DEFAULT '',
    reported_json TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT 'transcript'
);
CREATE TABLE IF NOT EXISTS turns (
    turn_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    ts INTEGER NOT NULL,
    model TEXT NOT NULL,
    models_json TEXT NOT NULL DEFAULT '[]',
    exchange INTEGER NOT NULL DEFAULT 0,
    input INTEGER NOT NULL DEFAULT 0,
    cache_creation INTEGER NOT NULL DEFAULT 0,
    cache_write_5m INTEGER NOT NULL DEFAULT 0,
    cache_write_1h INTEGER NOT NULL DEFAULT 0,
    cache_read INTEGER NOT NULL DEFAULT 0,
    output INTEGER NOT NULL DEFAULT 0,
    thinking INTEGER NOT NULL DEFAULT 0,
    web_search INTEGER NOT NULL DEFAULT 0,
    web_fetch INTEGER NOT NULL DEFAULT 0,
    tool_calls INTEGER NOT NULL DEFAULT 0,
    lines_added INTEGER NOT NULL DEFAULT 0,
    lines_removed INTEGER NOT NULL DEFAULT 0,
    paths_json TEXT NOT NULL DEFAULT '[]',
    cwd TEXT NOT NULL DEFAULT '',
    tag TEXT,
    source TEXT NOT NULL DEFAULT 'transcript',
    estimated INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS prompts (
    session_id TEXT NOT NULL,
    ts INTEGER NOT NULL,
    project_path TEXT NOT NULL DEFAULT '',
    is_command INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, ts)
);
CREATE INDEX IF NOT EXISTS turns_session ON turns(session_id);
CREATE INDEX IF NOT EXISTS turns_ts ON turns(ts);
"""

_TURN_COLS = ("turn_id", "session_id", "ts", "model", "models_json", "exchange", "input", "cache_creation",
              "cache_write_5m", "cache_write_1h", "cache_read", "output", "thinking", "web_search",
              "web_fetch", "tool_calls", "lines_added", "lines_removed", "paths_json", "cwd", "tag",
              "source", "estimated")


def connect(path: pathlib.Path = LEDGER_PATH) -> sqlite3.Connection:
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript(_SCHEMA)
    db.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    db.commit()
    return db


def _session_row(s: SessionStats) -> dict:
    return {
        "session_id": s.session_id, "parent_session_id": s.parent_session_id,
        "is_subagent": int(s.is_subagent), "agent_type": s.agent_type,
        "attribution_skill": s.attribution_skill, "project_path": s.project_path,
        "session_name": s.session_name, "start_ts": s.start_ts, "end_ts": s.end_ts,
        "caveman": int(s.caveman), "caveman_mode": s.caveman_mode,
        "reported_json": json.dumps(s.reported or {}),
        "source": s.turns_raw[0].get("source", "transcript") if s.turns_raw else "transcript",
    }


def _turn_row(session_id: str, t: dict) -> dict:
    return {
        "turn_id": f"{session_id}:{t['rid']}", "session_id": session_id, "ts": t["ts"],
        "model": t.get("model", "unknown"), "models_json": json.dumps(t.get("models") or []),
        "exchange": t.get("exchange", 0), "input": t["input"], "cache_creation": t["cache_creation"],
        "cache_write_5m": t.get("cache_write_5m", 0), "cache_write_1h": t.get("cache_write_1h", 0),
        "cache_read": t["cache_read"], "output": t["output"], "thinking": t.get("thinking", 0),
        "web_search": t.get("web_search", 0), "web_fetch": t.get("web_fetch", 0),
        "tool_calls": t.get("tool_calls", 0), "lines_added": t.get("lines_added", 0),
        "lines_removed": t.get("lines_removed", 0), "paths_json": json.dumps(t.get("paths") or []),
        "cwd": t.get("cwd", ""), "tag": t.get("tag"), "source": t.get("source", "transcript"),
        "estimated": int(bool(t.get("estimated"))),
    }


def ingest(db: sqlite3.Connection, sessions: list[SessionStats]) -> None:
    """Upsert sessions and turns. Re-ingesting is a no-op; a turn only ever grows (the
    entry with more output wins, matching the parser's streaming dedup)."""
    with db:
        for s in sessions:
            row = _session_row(s)
            cols = ", ".join(row)
            updates = ", ".join(f"{k}=excluded.{k}" for k in row if k != "session_id")
            db.execute(f"INSERT INTO sessions ({cols}) VALUES ({', '.join('?' * len(row))}) "
                       f"ON CONFLICT(session_id) DO UPDATE SET {updates}", tuple(row.values()))
            for t in s.turns_raw:
                tr = _turn_row(s.session_id, t)
                updates = ", ".join(f"{k}=excluded.{k}" for k in _TURN_COLS if k != "turn_id")
                db.execute(f"INSERT INTO turns ({', '.join(_TURN_COLS)}) VALUES ({', '.join('?' * len(_TURN_COLS))}) "
                           f"ON CONFLICT(turn_id) DO UPDATE SET {updates} WHERE excluded.output >= turns.output",
                           tuple(tr[c] for c in _TURN_COLS))


def _turn_dict(r: sqlite3.Row) -> dict:
    t = {
        "rid": r["turn_id"].split(":", 1)[1], "ts": r["ts"], "model": r["model"],
        "models": json.loads(r["models_json"]), "exchange": r["exchange"],
        "input": r["input"], "cache_creation": r["cache_creation"],
        "cache_write_5m": r["cache_write_5m"], "cache_write_1h": r["cache_write_1h"],
        "cache_read": r["cache_read"], "output": r["output"], "thinking": r["thinking"],
        "web_search": r["web_search"], "web_fetch": r["web_fetch"],
        "tool_calls": r["tool_calls"], "lines_added": r["lines_added"], "lines_removed": r["lines_removed"],
        "paths": json.loads(r["paths_json"]), "cwd": r["cwd"], "tag": r["tag"],
        "source": r["source"], "estimated": bool(r["estimated"]),
    }
    t["cost"] = t["input"] + t["cache_creation"] + t["output"]
    return t


def load_sessions(db: sqlite3.Connection) -> list[SessionStats]:
    """Rebuild SessionStats (with turns_raw) for every session in the ledger."""
    turns: dict[str, list] = {}
    for r in db.execute("SELECT * FROM turns ORDER BY ts, turn_id"):
        turns.setdefault(r["session_id"], []).append(_turn_dict(r))
    out = []
    for r in db.execute("SELECT * FROM sessions ORDER BY start_ts, session_id"):
        s = SessionStats(
            session_id=r["session_id"], project_path=r["project_path"], session_name=r["session_name"],
            caveman=bool(r["caveman"]), caveman_mode=r["caveman_mode"],
            start_ts=r["start_ts"], end_ts=r["end_ts"],
            is_subagent=bool(r["is_subagent"]), parent_session_id=r["parent_session_id"],
            agent_type=r["agent_type"], attribution_skill=r["attribution_skill"],
            reported=json.loads(r["reported_json"]),
        )
        s.turns_raw = turns.get(s.session_id, [])
        recompute_totals(s)
        if s.turns:
            out.append(s)
    return out
