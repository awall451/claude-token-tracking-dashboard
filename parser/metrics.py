"""
Per-project (and per-tag) rollups over a date range: the numbers behind the Projects view.

Works on sessions whose turns are already attributed (`project`, `tags`), priced
(`api_usd`, `sub_usd`) and energy-annotated (`wh`). Everything is summed per turn, so any
date range or grouping stays consistent. No composite "complexity score" — the signals
(files touched, lines changed, tool calls, subagents, active time) are reported as-is.
"""

from collections import Counter, defaultdict
from datetime import UTC, datetime
from itertools import pairwise

IDLE_GAP_MIN = 15   # a gap longer than this between turns counts as idle, not work


def _day(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def active_minutes(turns: list[dict], idle_gap_min: float = IDLE_GAP_MIN) -> float:
    """Sum of gaps between consecutive turns that are shorter than the idle threshold."""
    ts = sorted(t["ts"] for t in turns)
    limit = idle_gap_min * 60_000
    return sum(b - a for a, b in pairwise(ts) if b - a <= limit) / 60_000


def _group_key(t: dict, group: str) -> str | None:
    if group.startswith("tag:"):
        return (t.get("tags") or {}).get(group[4:])
    return t.get("project")


def _empty() -> dict:
    return {"turns": 0, "api_usd": 0.0, "estimated_api_usd": 0.0, "sub_usd": 0.0, "wh": [0.0, 0.0, 0.0],
            "tokens": Counter(), "models": Counter(), "tool_calls": 0, "lines_added": 0, "lines_removed": 0,
            "caveman_turns": 0, "first_ts": None, "last_ts": None,
            "_sessions": set(), "_subagents": set(), "_prompts": set(), "_files": set(), "_days": set(),
            "_turns_by_session": defaultdict(list)}


def _add(g: dict, s, t: dict) -> None:
    root = s.parent_session_id if s.is_subagent else s.session_id
    g["turns"] += 1
    g["api_usd"] += t.get("api_usd") or 0.0
    g["sub_usd"] += t.get("sub_usd") or 0.0
    if t.get("estimated"):
        g["estimated_api_usd"] += t.get("api_usd") or 0.0
    for i, x in enumerate(t.get("wh") or (0, 0, 0)):
        g["wh"][i] += x
    g["tokens"].update({k: t[k] for k in ("input", "cache_creation", "cache_read", "output")})
    g["models"][t.get("model", "unknown")] += 1
    for k in ("tool_calls", "lines_added", "lines_removed"):
        g[k] += t.get(k, 0)
    g["caveman_turns"] += int(bool(s.caveman))
    g["first_ts"] = min(filter(None, (g["first_ts"], t["ts"])))
    g["last_ts"] = max(filter(None, (g["last_ts"], t["ts"])))
    g["_sessions"].add(root)
    if s.is_subagent:
        g["_subagents"].add(s.session_id)
    else:
        # estimated turns are one per prompt; exact turns share their prompt's exchange index
        g["_prompts"].add((root, t["ts"] if t.get("estimated") else t.get("exchange", 0)))
    g["_files"].update(t.get("paths") or [])
    g["_days"].add(_day(t["ts"]))
    g["_turns_by_session"][s.session_id].append(t)


def _finish(g: dict) -> dict:
    active = sum(active_minutes(ts) for ts in g["_turns_by_session"].values())
    out = {k: v for k, v in g.items() if not k.startswith("_")}
    out.update(sessions=len(g["_sessions"]), subagents=len(g["_subagents"]), prompts=len(g["_prompts"]),
               files_touched=len(g["_files"]), active_days=len(g["_days"]), active_hours=round(active / 60, 2),
               tokens=dict(g["tokens"]), models=dict(g["models"]),
               avg_context=round(sum(g["tokens"].get(k, 0) for k in ("input", "cache_creation", "cache_read"))
                                 / g["turns"]) if g["turns"] else 0)
    for k in ("api_usd", "estimated_api_usd", "sub_usd"):
        out[k] = round(out[k], 4)
    out["wh"] = [round(x, 3) for x in out["wh"]]
    return out


def _in_range(t: dict, start_ms, end_ms) -> bool:
    return (start_ms is None or t["ts"] >= start_ms) and (end_ms is None or t["ts"] < end_ms)


def by_project(sessions: list, group: str = "project", start_ms=None, end_ms=None) -> dict:
    """{group value: metrics} for turns in [start_ms, end_ms). group: 'project' | 'tag:<key>'."""
    groups = defaultdict(_empty)
    for s in sessions:
        for t in s.turns_raw:
            key = _group_key(t, group)
            if key is not None and _in_range(t, start_ms, end_ms):
                _add(groups[key], s, t)
    return {k: _finish(g) for k, g in sorted(groups.items(), key=lambda kv: -kv[1]["api_usd"])}


def project_detail(sessions: list, project: str, start_ms=None, end_ms=None) -> dict:
    """Summary, per-day series and per-session breakdown for one project."""
    summary = _empty()
    daily = defaultdict(lambda: {"api_usd": 0.0, "estimated_api_usd": 0.0, "cost_tokens": 0, "wh": 0.0, "turns": 0})
    per_session = {}
    for s in sessions:
        turns = [t for t in s.turns_raw if t.get("project") == project and _in_range(t, start_ms, end_ms)]
        if not turns:
            continue
        g = _empty()
        for t in turns:
            _add(summary, s, t)
            _add(g, s, t)
            d = daily[_day(t["ts"])]
            d["api_usd"] += t.get("api_usd") or 0.0
            d["estimated_api_usd"] += (t.get("api_usd") or 0.0) if t.get("estimated") else 0.0
            d["cost_tokens"] += t["cost"]
            d["wh"] += (t.get("wh") or (0, 0, 0))[1]
            d["turns"] += 1
        per_session[s.session_id] = {"session_id": s.session_id, "session_name": s.session_name,
                                     "is_subagent": s.is_subagent, "parent_session_id": s.parent_session_id,
                                     "estimated": s.estimated, **_finish(g)}
    return {
        "project": project,
        "summary": _finish(summary),
        "daily": [{"date": day, **v} for day, v in sorted(daily.items())],
        "sessions": sorted(per_session.values(), key=lambda x: -(x["last_ts"] or 0)),
    }
