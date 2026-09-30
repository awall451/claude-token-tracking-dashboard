#!/usr/bin/env python3
"""
Backfill the ledger with history that predates it.

Claude Code deletes transcripts after `cleanupPeriodDays`, so older usage survives only in:
  1. ~/.claude/history.jsonl — every prompt typed (session, project, timestamp); no tokens
  2. ~/.claude.json projects.*.last* — exact usage of each project's most recent session
  3. old tracker stats.json snapshots — exact per-turn usage for the sessions they cover

Exact sources (2, 3) are imported as-is. Sessions known only from history.jsonl get
*estimated* turns — one per prompt, sized by the median tokens-per-prompt of exact
sessions (same project, else all projects) — flagged `estimated` so the UI can label them.
Estimates are deleted and recomputed on every run, so they improve as exact data grows,
and are only made for sessions that ended before exact transcript coverage begins
(later sessions without transcripts had no model calls). Prompt text is never stored.

Usage: python3 parser/backfill.py --ledger ledger.sqlite [--snapshot old-stats.json]
"""

import argparse
import json
import pathlib
import statistics
from collections import Counter, defaultdict
from datetime import UTC, datetime

try:
    from . import ledger
    from .models import SessionStats, recompute_totals
except ImportError:
    import ledger
    from models import SessionStats, recompute_totals

CLASSES = ("input", "cache_creation", "cache_read", "output")
_USAGE_KEYS = {"input": "inputTokens", "cache_creation": "cacheCreationInputTokens",
               "cache_read": "cacheReadInputTokens", "output": "outputTokens"}
_TOTAL_KEYS = {"input": "lastTotalInputTokens", "cache_creation": "lastTotalCacheCreationInputTokens",
               "cache_read": "lastTotalCacheReadInputTokens", "output": "lastTotalOutputTokens"}


def _turn(rid, ts, model, tokens: dict, source: str, estimated=False) -> dict:
    t = {"rid": rid, "ts": ts, "model": model, "models": [model], "source": source, "estimated": estimated,
         **{c: int(tokens.get(c, 0)) for c in CLASSES}}
    t["cost"] = t["input"] + t["cache_creation"] + t["output"]
    return t


def _session(sid, project, turns, **meta) -> SessionStats:
    s = SessionStats(session_id=sid, project_path=project, **meta)
    s.turns_raw = turns
    s.start_ts = min(t["ts"] for t in turns)
    s.end_ts = max(t["ts"] for t in turns)
    recompute_totals(s)
    return s


# --- history.jsonl → prompts ------------------------------------------------------------

def record_prompts(db, history_path: pathlib.Path) -> None:
    rows = []
    for line in history_path.read_text(errors="replace").splitlines():
        try:
            o = json.loads(line)
        except json.JSONDecodeError:
            continue
        if o.get("sessionId") and o.get("timestamp"):
            is_cmd = str(o.get("display", "")).lstrip().startswith("/")
            rows.append((o["sessionId"], int(o["timestamp"]), o.get("project", ""), int(is_cmd)))
    with db:
        db.executemany("INSERT OR IGNORE INTO prompts VALUES (?, ?, ?, ?)", rows)


def _prompts_by_session(db) -> dict[str, list]:
    out = defaultdict(list)
    for r in db.execute("SELECT * FROM prompts WHERE is_command = 0 ORDER BY ts"):
        out[r["session_id"]].append(r)
    return out


def _known_sessions(db) -> set[str]:
    """Sessions with exact data. Estimated sessions don't count: exact sources replace them."""
    return {r[0] for r in db.execute("SELECT session_id FROM sessions WHERE source != 'estimate'")}


def _clear_estimates(db) -> None:
    with db:
        db.execute("DELETE FROM turns WHERE estimated = 1")
        db.execute("DELETE FROM sessions WHERE source = 'estimate'")


# --- ~/.claude.json last-session usage ----------------------------------------------------

def _claude_json_turns(info: dict, ts: int) -> list[dict]:
    usage = info.get("lastModelUsage") or {}
    turns = [_turn(f"cj:{m}", ts, m, {c: u.get(k, 0) for c, k in _USAGE_KEYS.items()}, "claude_json")
             for m, u in usage.items()]
    if not turns and any(info.get(k) for k in _TOTAL_KEYS.values()):
        turns = [_turn("cj:total", ts, "unknown", {c: info.get(k, 0) for c, k in _TOTAL_KEYS.items()}, "claude_json")]
    if turns:
        turns[0]["lines_added"] = info.get("lastLinesAdded", 0)
        turns[0]["lines_removed"] = info.get("lastLinesRemoved", 0)
    return turns


def import_claude_json(db, claude_json_path: pathlib.Path, prompts: dict) -> None:
    projects = json.loads(claude_json_path.read_text()).get("projects", {})
    known = _known_sessions(db)
    sessions = []
    for project, info in projects.items():
        sid = info.get("lastSessionId")
        if not sid or sid in known or sid not in prompts:
            continue
        turns = _claude_json_turns(info, prompts[sid][0]["ts"])
        if turns:
            sessions.append(_session(sid, project, turns, reported={"cost_usd": round(info.get("lastCost", 0), 4)}))
    ledger.ingest(db, sessions)


# --- old stats.json snapshots ---------------------------------------------------------------

def import_snapshot(db, snapshot_path: pathlib.Path) -> None:
    _clear_estimates(db)   # recomputed by the next run(); exact data takes precedence
    known = _known_sessions(db)
    sessions = []
    for s in json.loads(snapshot_path.read_text()).get("sessions", []):
        if s["session_id"] in known or not s.get("turns_raw"):
            continue
        model = Counter(s.get("models") or {"unknown": 1}).most_common(1)[0][0]
        turns = [_turn(f"snap{i}", t["ts"], model, t, "snapshot") for i, t in enumerate(s["turns_raw"])]
        sessions.append(_session(s["session_id"], s.get("project_path", ""), turns,
                                 caveman=s.get("caveman", False), caveman_mode=s.get("caveman_mode", "")))
    ledger.ingest(db, sessions)


# --- estimates ------------------------------------------------------------------------------

def _exact_per_prompt(db, prompts: dict) -> list[tuple[str, dict, str]]:
    """(project, per-prompt tokens by class, dominant model) for each exact main session,
    folding its subagents' tokens into the parent."""
    totals = defaultdict(lambda: Counter())
    models = defaultdict(Counter)
    for s in ledger.load_sessions(db):
        if any(t["estimated"] for t in s.turns_raw):
            continue
        key = s.parent_session_id or s.session_id
        totals[key].update({c: getattr(s, f) for c, f in (("input", "input_tokens"), ("cache_creation",
                           "cache_creation_tokens"), ("cache_read", "cache_read_tokens"), ("output", "output_tokens"))})
        models[key].update(s.models)
        if not s.is_subagent:
            totals[key]["_project"] = s.project_path
    out = []
    for sid, tot in totals.items():
        n = len(prompts.get(sid, []))
        if n and "_project" in tot:
            out.append((tot["_project"], {c: tot[c] / n for c in CLASSES}, models[sid].most_common(1)[0][0]))
    return out


def _profiles(samples) -> dict:
    """Median per-prompt tokens by class, per project and globally ('*')."""
    grouped = defaultdict(list)
    for project, per_prompt, _model in samples:
        grouped[project].append(per_prompt)
        grouped["*"].append(per_prompt)
    return {p: {c: round(statistics.median(x[c] for x in rows)) for c in CLASSES} for p, rows in grouped.items()}


def _monthly_models(db) -> list[tuple[float, str]]:
    """(token-weighted mean ts, dominant model by tokens) for each month of exact data.
    Weighting by tokens keeps frequent-but-tiny side calls (e.g. Haiku titles) from winning."""
    tokens = defaultdict(Counter)
    ts_sum = defaultdict(float)
    for ts, model, tok in db.execute("SELECT ts, model, input + cache_creation + cache_read + output FROM turns "
                                     "WHERE estimated = 0 AND model NOT IN ('unknown', '<synthetic>')"):
        month = datetime.fromtimestamp(ts / 1000, tz=UTC).strftime("%Y-%m")
        tokens[month][model] += tok
        ts_sum[month] += ts * tok
    return [(ts_sum[m] / max(sum(c.values()), 1), c.most_common(1)[0][0]) for m, c in tokens.items()]


def _model_at(ts: int, monthly: list[tuple[float, str]]) -> str:
    return min(monthly, key=lambda mm: abs(mm[0] - ts))[1] if monthly else "unknown"


def _coverage_start(db) -> int | None:
    return db.execute("SELECT MIN(ts) FROM turns WHERE source = 'transcript'").fetchone()[0]


def estimate(db, prompts: dict) -> None:
    start = _coverage_start(db)
    samples = _exact_per_prompt(db, prompts)
    if start is None or not samples:
        return
    tokens = _profiles(samples)
    monthly = _monthly_models(db)
    known = _known_sessions(db)
    sessions = []
    for sid, rows in prompts.items():
        if sid in known or rows[-1]["ts"] >= start:
            continue
        project = rows[0]["project_path"]
        key = project if project in tokens else "*"
        model = _model_at(rows[0]["ts"], monthly)
        turns = [_turn(f"est:{r['ts']}", r["ts"], model, tokens[key], "estimate", estimated=True) for r in rows]
        sessions.append(_session(sid, project, turns))
    ledger.ingest(db, sessions)


def run(db, history_path: pathlib.Path | None, claude_json_path: pathlib.Path | None) -> None:
    """Full backfill pass: prompts → exact last-session usage → estimates."""
    if history_path and history_path.exists():
        record_prompts(db, history_path)
    prompts = _prompts_by_session(db)
    _clear_estimates(db)
    if claude_json_path and claude_json_path.exists():
        import_claude_json(db, claude_json_path, prompts)
    estimate(db, prompts)


def main():
    home = pathlib.Path.home()
    ap = argparse.ArgumentParser(description="Backfill the token ledger with pre-ledger history")
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--history", default=str(home / ".claude" / "history.jsonl"))
    ap.add_argument("--claude-json", default=str(home / ".claude.json"))
    ap.add_argument("--snapshot", action="append", default=[], help="Old tracker stats.json (repeatable)")
    args = ap.parse_args()
    db = ledger.connect(pathlib.Path(args.ledger))
    for snap in args.snapshot:
        import_snapshot(db, pathlib.Path(snap))
    run(db, pathlib.Path(args.history), pathlib.Path(args.claude_json))
    n = db.execute("SELECT source, COUNT(*), SUM(estimated) FROM turns GROUP BY source").fetchall()
    print({r[0]: r[1] for r in n})


if __name__ == "__main__":
    main()
