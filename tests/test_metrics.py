import pytest

from parser import metrics
from parser.models import SessionStats, recompute_totals

MIN = 60 * 1000
T0 = 1790676000000  # 2026-09-29T10:00Z


def _t(ts, project, **kw):
    t = {"ts": ts, "model": "claude-opus-5-5", "input": 1, "cache_creation": 10, "cache_read": 100,
         "output": 5, "cost": 16, "project": project, "tags": {}, "exchange": 0, "paths": [],
         "tool_calls": 0, "lines_added": 0, "lines_removed": 0, "api_usd": 1.0, "sub_usd": 0.5,
         "wh": [1.0, 2.0, 4.0], "estimated": False}
    t.update(kw)
    return t


def _s(sid, turns, **kw):
    s = SessionStats(session_id=sid, project_path="/hub", **kw)
    s.turns_raw = turns
    recompute_totals(s)
    s.start_ts = min(t["ts"] for t in turns)
    s.end_ts = max(t["ts"] for t in turns)
    return s


def test_active_hours_counts_only_short_gaps():
    turns = [_t(T0, "a"), _t(T0 + 10 * MIN, "a"), _t(T0 + 12 * MIN, "a"),   # 12 min active
             _t(T0 + 120 * MIN, "a"), _t(T0 + 125 * MIN, "a")]               # idle gap, +5 min
    assert metrics.active_minutes(turns, idle_gap_min=15) == pytest.approx(17)


def test_project_rollup_core_fields():
    a1 = _s("s1", [_t(T0, "a", exchange=0, paths=["/x/1", "/x/2"], lines_added=10, tool_calls=3),
                   _t(T0 + MIN, "a", exchange=1, paths=["/x/2"], lines_removed=4),
                   _t(T0 + 2 * MIN, "b", exchange=2)], caveman=True)
    sub = _s("agent-1", [_t(T0 + MIN, "a", paths=["/x/3"])], is_subagent=True, parent_session_id="s1")
    est = _s("old", [_t(T0 - 40 * 24 * 60 * MIN, "a", estimated=True, api_usd=3.0)])
    out = metrics.by_project([a1, sub, est], group="project")
    a = out["a"]
    assert a["turns"] == 4
    assert a["sessions"] == 2            # s1 + old; the subagent folds into its parent
    assert a["subagents"] == 1
    assert a["prompts"] == 3             # s1 exchanges 0,1 + one estimated prompt
    assert a["api_usd"] == pytest.approx(6.0)
    assert a["estimated_api_usd"] == pytest.approx(3.0)
    assert a["sub_usd"] == pytest.approx(2.0)
    assert a["wh"] == pytest.approx([4.0, 8.0, 16.0])
    assert a["files_touched"] == 3
    assert a["lines_added"] == 10 and a["lines_removed"] == 4 and a["tool_calls"] == 3
    assert a["tokens"]["cache_read"] == 400
    assert a["models"] == {"claude-opus-5-5": 4}
    assert a["active_days"] == 2
    assert a["caveman_turns"] == 2
    assert out["b"]["turns"] == 1


def test_date_range_filter_and_tag_grouping():
    s = _s("s1", [_t(T0, "a", tags={"client": "acme"}), _t(T0 + 3 * 24 * 60 * MIN, "a", tags={"client": "acme"}),
                  _t(T0, "b")])
    out = metrics.by_project([s], group="tag:client", start_ms=T0 + MIN)
    assert list(out) == ["acme"] and out["acme"]["turns"] == 1


def test_project_detail_daily_and_sessions():
    s = _s("s1", [_t(T0, "a"), _t(T0 + 24 * 60 * MIN, "a", estimated=True)], session_name="work")
    d = metrics.project_detail([s], "a")
    assert [x["date"] for x in d["daily"]] == ["2026-09-29", "2026-09-30"]
    assert d["daily"][1]["estimated_api_usd"] == pytest.approx(1.0)
    assert d["sessions"][0]["session_id"] == "s1" and d["sessions"][0]["session_name"] == "work"
    assert d["summary"]["turns"] == 2
