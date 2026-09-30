import pytest

from parser.models import SessionStats, recompute_totals
from server import api

DAY = 24 * 3600 * 1000
NOW = 1790676000000


def _t(ts, project, **kw):
    t = {"rid": str(ts), "ts": ts, "model": "claude-opus-5-5", "input": 1, "cache_creation": 0, "cache_read": 10,
         "output": 1, "cost": 2, "project": project, "tags": {"client": "acme"}, "exchange": 0, "paths": ["/x"],
         "api_usd": 1.0, "sub_usd": None, "wh": [1, 2, 3], "estimated": False, "cwd": "", "tag": None}
    t.update(kw)
    return t


def _sessions():
    a = SessionStats(session_id="s1", project_path="/hub", session_name="one")
    a.turns_raw = [_t(NOW - 2 * DAY, "site"), _t(NOW - 40 * DAY, "site"), _t(NOW - DAY, "api")]
    b = SessionStats(session_id="s2", project_path="/hub")
    b.turns_raw = [_t(NOW - 60 * DAY, "api", estimated=True)]
    for s in (a, b):
        recompute_totals(s)
        s.start_ts = min(t["ts"] for t in s.turns_raw)
        s.end_ts = max(t["ts"] for t in s.turns_raw)
    return [a, b]


def test_projects_range_and_totals():
    out = api.projects(_sessions(), {"range": ["30d"]}, now_ms=NOW)
    names = [p["name"] for p in out["projects"]]
    assert names == ["site", "api"] or names == ["api", "site"]
    assert out["totals"]["api_usd"] == pytest.approx(2.0)
    assert out["range"] == "30d"
    assert out["tag_keys"] == ["client"]
    site = next(p for p in out["projects"] if p["name"] == "site")
    assert site["tags"] == {"client": "acme"}


def test_projects_all_time_and_tag_group():
    out = api.projects(_sessions(), {"range": ["all"], "group": ["tag:client"]}, now_ms=NOW)
    assert [p["name"] for p in out["projects"]] == ["acme"]
    assert out["projects"][0]["api_usd"] == pytest.approx(4.0)
    assert out["totals"]["estimated_api_usd"] == pytest.approx(1.0)


def test_bad_range_falls_back_to_default():
    assert api.projects(_sessions(), {"range": ["bogus"]}, now_ms=NOW)["range"] == "30d"


def test_project_detail():
    out = api.project(_sessions(), "site", {"range": ["all"]}, now_ms=NOW)
    assert out["summary"]["turns"] == 2
    assert out["tags"] == {"client": "acme"}
    assert len(out["daily"]) == 2


def test_sessions_filtered_by_project_without_turns():
    out = api.sessions(_sessions(), {"project": ["api"], "range": ["all"]}, now_ms=NOW)
    assert [s["session_id"] for s in out["sessions"]] == ["s1", "s2"]
    assert "turns_raw" not in out["sessions"][0]


def test_session_detail_has_turns():
    out = api.session(_sessions(), "s1")
    assert len(out["turns_raw"]) == 3
    assert api.session(_sessions(), "nope") is None


def test_project_tags_come_from_most_recent_turn():
    s = SessionStats(session_id="s", project_path="/p")
    s.turns_raw = [_t(NOW - DAY, "moved", tags={"client": "new"}), _t(NOW - 90 * DAY, "moved", tags={"client": "old"})]
    recompute_totals(s)
    assert api.project([s], "moved", {"range": ["all"]}, now_ms=NOW)["tags"] == {"client": "new"}
