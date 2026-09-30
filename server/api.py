"""
JSON API handlers over the annotated session list (pure functions; serve.py does the HTTP).

  GET /api/projects?range=30d&group=project|tag:<key>
  GET /api/project?name=<name>&range=30d
  GET /api/sessions?project=<name>&range=all
  GET /api/session?id=<session id>

`range` is one of RANGES (default 30d). Queries are parse_qs-style dicts ({key: [values]}).
"""

import time

try:
    from parser import metrics
except ImportError:  # running as server/serve.py with parser/ on sys.path
    import metrics

DAY_MS = 24 * 3600 * 1000
RANGES = {"7d": 7, "30d": 30, "90d": 90, "all": None}
DEFAULT_RANGE = "30d"


def _q(query: dict, key: str, default=None):
    return (query.get(key) or [default])[0]


def _range(query: dict, now_ms: int) -> tuple[str, int | None]:
    name = _q(query, "range", DEFAULT_RANGE)
    if name not in RANGES:
        name = DEFAULT_RANGE
    days = RANGES[name]
    return name, (now_ms - days * DAY_MS) if days else None


def _now(now_ms):
    return now_ms if now_ms is not None else int(time.time() * 1000)


def _project_tags(sessions: list) -> dict:
    """Tags per project from its most recent turn (a project moved between hubs keeps its new tags)."""
    latest: dict[str, tuple[int, dict]] = {}
    for s in sessions:
        for t in s.turns_raw:
            p = t.get("project")
            if p and (p not in latest or t["ts"] > latest[p][0]):
                latest[p] = (t["ts"], t.get("tags") or {})
    return {p: tags for p, (_, tags) in latest.items()}


def _tag_keys(sessions: list) -> list[str]:
    return sorted({k for s in sessions for t in s.turns_raw for k in (t.get("tags") or {})})


def _sum(rows: list, key: str) -> float:
    return round(sum(r[key] for r in rows), 4)


def projects(sessions: list, query: dict, now_ms: int | None = None) -> dict:
    name, start = _range(query, _now(now_ms))
    group = _q(query, "group", "project")
    rows = metrics.by_project(sessions, group=group, start_ms=start)
    tags = _project_tags(sessions) if group == "project" else {}
    out_rows = [{"name": k, "tags": tags.get(k, {}), **v} for k, v in rows.items()]
    return {
        "range": name, "group": group, "tag_keys": _tag_keys(sessions),
        "totals": {k: _sum(out_rows, k) for k in ("api_usd", "estimated_api_usd", "sub_usd", "active_hours")}
                  | {"wh": [round(sum(r["wh"][i] for r in out_rows), 3) for i in range(3)],
                     "projects": len(out_rows)},
        "projects": out_rows,
    }


def project(sessions: list, name: str, query: dict, now_ms: int | None = None) -> dict:
    rng, start = _range(query, _now(now_ms))
    detail = metrics.project_detail(sessions, name, start_ms=start)
    return {"range": rng, "tags": _project_tags(sessions).get(name, {}), **detail}


def _summary(s) -> dict:
    d = s.to_dict()
    d.pop("turns_raw", None)
    return d


def sessions(all_sessions: list, query: dict, now_ms: int | None = None) -> dict:
    rng, start = _range(query, _now(now_ms))
    proj = _q(query, "project")
    out = []
    for s in all_sessions:
        turns = [t for t in s.turns_raw if start is None or t["ts"] >= start]
        if proj is not None:
            turns = [t for t in turns if t.get("project") == proj]
        if turns:
            out.append((max(t["ts"] for t in turns), _summary(s)))
    return {"range": rng, "project": proj, "sessions": [d for _, d in sorted(out, key=lambda x: -x[0])]}


def session(all_sessions: list, session_id: str) -> dict | None:
    for s in all_sessions:
        if s.session_id == session_id:
            return s.to_dict()
    return None
