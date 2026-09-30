import json

from parser import backfill, ledger
from parser.models import SessionStats, recompute_totals

DAY = 24 * 3600 * 1000
T0 = 1790676000000  # 2026-09-29T10:00Z — start of exact (transcript) coverage in these tests


EXACT_TOKENS = (10, 100, 1000, 50)  # input, cache write, cache read, output


def _exact_session(db, sid, project, ts):
    s = SessionStats(session_id=sid, project_path=project, start_ts=ts, end_ts=ts)
    inp, cc, cr, out = EXACT_TOKENS
    model = "claude-opus-5-5"
    s.turns_raw = [{"rid": "r1", "ts": ts, "model": model, "models": [model], "input": inp,
                    "cache_creation": cc, "cache_read": cr, "output": out, "cost": inp + cc + out}]
    recompute_totals(s)
    ledger.ingest(db, [s])


def _history(tmp_path, rows):
    p = tmp_path / "history.jsonl"
    p.write_text("".join(json.dumps({"display": d, "pastedContents": {}, "timestamp": ts,
                                     "project": proj, "sessionId": sid}) + "\n"
                         for sid, proj, ts, d in rows))
    return p


def _claude_json(tmp_path, projects):
    p = tmp_path / "claude.json"
    p.write_text(json.dumps({"projects": projects}))
    return p


def _by_id(db):
    return {s.session_id: s for s in ledger.load_sessions(db)}


def test_prompts_recorded_without_text(tmp_path):
    db = ledger.connect(tmp_path / "l.sqlite")
    hist = _history(tmp_path, [("a", "/p", T0, "secret prompt"), ("a", "/p", T0 + 1, "/clear")])
    backfill.run(db, history_path=hist, claude_json_path=None)
    rows = db.execute("SELECT * FROM prompts ORDER BY ts").fetchall()
    assert [(r["session_id"], r["is_command"]) for r in rows] == [("a", 0), ("a", 1)]
    assert "secret" not in json.dumps([dict(r) for r in rows])


def test_estimates_only_for_sessions_before_exact_coverage(tmp_path):
    db = ledger.connect(tmp_path / "l.sqlite")
    _exact_session(db, "exact", "/p", T0)
    hist = _history(tmp_path, [
        ("exact", "/p", T0, "hi"), ("exact", "/p", T0 + 1000, "more"),        # 2 prompts → profile
        ("old", "/p", T0 - 30 * DAY, "a"), ("old", "/p", T0 - 30 * DAY + 5, "b"),
        ("old", "/p", T0 - 30 * DAY + 9, "/exit"),                            # command: not estimated
        ("recent-empty", "/p", T0 + DAY, "x"),                                 # after coverage: skip
    ])
    backfill.run(db, history_path=hist, claude_json_path=None)
    by_id = _by_id(db)
    assert "recent-empty" not in by_id
    old = by_id["old"]
    assert old.turns == 2 and all(t["estimated"] for t in old.turns_raw)
    # exact profile: 1160 tokens over 2 prompts → 580 per prompt, class mix preserved
    assert old.output_tokens == 2 * 25
    assert old.cache_read_tokens == 2 * 500
    assert old.project_path == "/p"


def test_estimate_uses_global_profile_for_unknown_project(tmp_path):
    db = ledger.connect(tmp_path / "l.sqlite")
    _exact_session(db, "exact", "/p", T0)
    hist = _history(tmp_path, [("exact", "/p", T0, "hi"), ("new", "/other", T0 - DAY, "x")])
    backfill.run(db, history_path=hist, claude_json_path=None)
    assert _by_id(db)["new"].output_tokens == 50


def test_estimates_are_recomputed_not_accumulated(tmp_path):
    db = ledger.connect(tmp_path / "l.sqlite")
    _exact_session(db, "exact", "/p", T0)
    hist = _history(tmp_path, [("exact", "/p", T0, "hi"), ("old", "/p", T0 - DAY, "x")])
    backfill.run(db, history_path=hist, claude_json_path=None)
    backfill.run(db, history_path=hist, claude_json_path=None)
    assert _by_id(db)["old"].turns == 1


def test_claude_json_last_session_is_exact_and_beats_estimate(tmp_path):
    db = ledger.connect(tmp_path / "l.sqlite")
    _exact_session(db, "exact", "/p", T0)
    hist = _history(tmp_path, [("exact", "/p", T0, "hi"), ("last", "/q", T0 - 10 * DAY, "go")])
    cj = _claude_json(tmp_path, {"/q": {
        "lastSessionId": "last", "lastCost": 9.05, "lastLinesAdded": 20, "lastLinesRemoved": 3,
        "lastModelUsage": {
            "claude-sonnet-4-6": {"inputTokens": 1, "outputTokens": 2, "cacheReadInputTokens": 3,
                                  "cacheCreationInputTokens": 4, "webSearchRequests": 0, "costUSD": 9.0},
            "claude-haiku-4-5": {"inputTokens": 5, "outputTokens": 6, "cacheReadInputTokens": 0,
                                 "cacheCreationInputTokens": 0, "webSearchRequests": 0, "costUSD": 0.05}},
    }})
    backfill.run(db, history_path=hist, claude_json_path=cj)
    last = _by_id(db)["last"]
    assert not any(t["estimated"] for t in last.turns_raw)
    assert last.turns == 2 and last.output_tokens == 8
    assert last.reported["cost_usd"] == 9.05
    assert sum(t["lines_added"] for t in last.turns_raw) == 20
    assert last.start_ts == T0 - 10 * DAY


def test_claude_json_skips_sessions_already_exact(tmp_path):
    db = ledger.connect(tmp_path / "l.sqlite")
    _exact_session(db, "exact", "/p", T0)
    cj = _claude_json(tmp_path, {"/p": {"lastSessionId": "exact", "lastCost": 1,
                                        "lastModelUsage": {"m": {"inputTokens": 999, "outputTokens": 999}}}})
    backfill.run(db, history_path=None, claude_json_path=cj)
    assert _by_id(db)["exact"].output_tokens == 50


def test_snapshot_import(tmp_path):
    db = ledger.connect(tmp_path / "l.sqlite")
    snap = tmp_path / "stats.json"
    snap.write_text(json.dumps({"sessions": [{
        "session_id": "snap1", "project_path": "/p", "start_ts": T0 - 100 * DAY, "end_ts": T0 - 100 * DAY,
        "caveman": False, "caveman_mode": "", "models": {"claude-sonnet-4-6": 2},
        "turns_raw": [{"ts": T0 - 100 * DAY, "input": 1, "cache_creation": 2, "cache_read": 3, "output": 4, "cost": 7},
                      {"ts": T0 - 100 * DAY + 5, "input": 1, "cache_creation": 2, "cache_read": 3, "output": 4, "cost": 7}],
    }]}))
    backfill.import_snapshot(db, snap)
    backfill.import_snapshot(db, snap)
    s = _by_id(db)["snap1"]
    assert s.turns == 2 and s.output_tokens == 8
    assert s.turns_raw[0]["source"] == "snapshot" and s.turns_raw[0]["model"] == "claude-sonnet-4-6"


def test_estimated_sessions_flagged_and_excluded_from_caveman():
    from parser.models import aggregate_by_day, caveman_comparison
    est = SessionStats(session_id="e", project_path="/p")
    est.turns_raw = [{"rid": "x", "ts": T0, "model": "m", "input": 1, "cache_creation": 0, "cache_read": 0,
                      "output": 1, "cost": 2, "estimated": True, "source": "estimate"}]
    recompute_totals(est)
    est.start_ts = T0
    assert est.to_dict()["estimated"] is True
    assert caveman_comparison([est])["normal"]["sessions"] == 0
    day = aggregate_by_day([est])["2026-09-29"]
    assert day["estimated_cost_tokens"] == 2


def test_exact_sources_replace_earlier_estimates(tmp_path):
    db = ledger.connect(tmp_path / "l.sqlite")
    _exact_session(db, "exact", "/p", T0)
    hist = _history(tmp_path, [("exact", "/p", T0, "hi"), ("snap1", "/p", T0 - 100 * DAY, "x"),
                               ("last", "/q", T0 - 10 * DAY, "go")])
    backfill.run(db, history_path=hist, claude_json_path=None)      # both become estimates
    assert _by_id(db)["snap1"].estimated and _by_id(db)["last"].estimated
    cj = _claude_json(tmp_path, {"/q": {"lastSessionId": "last", "lastCost": 1,
                                        "lastModelUsage": {"m": {"inputTokens": 1, "outputTokens": 7}}}})
    backfill.run(db, history_path=hist, claude_json_path=cj)
    snap = tmp_path / "stats.json"
    snap.write_text(json.dumps({"sessions": [{"session_id": "snap1", "project_path": "/p", "models": {"m": 1},
        "turns_raw": [{"ts": T0 - 100 * DAY, "input": 1, "cache_creation": 0, "cache_read": 0, "output": 3, "cost": 4}]}]}))
    backfill.import_snapshot(db, snap)
    by_id = _by_id(db)
    assert not by_id["last"].estimated and by_id["last"].output_tokens == 7
    assert not by_id["snap1"].estimated and by_id["snap1"].output_tokens == 3


def test_estimate_model_is_that_months_dominant_model_by_tokens(tmp_path):
    db = ledger.connect(tmp_path / "l.sqlite")
    _exact_session(db, "exact", "/p", T0)                                  # Sep: opus-5-5
    april = T0 - 160 * DAY
    s = SessionStats(session_id="apr", project_path="/q", start_ts=april, end_ts=april)
    s.turns_raw = [
        {"rid": "a", "ts": april, "model": "claude-sonnet-4-6", "input": 1, "cache_creation": 0, "cache_read": 900,
         "output": 1, "cost": 2, "source": "claude_json"},
        {"rid": "b", "ts": april, "model": "claude-haiku-4-5", "input": 50, "cache_creation": 0, "cache_read": 0,
         "output": 5, "cost": 55, "source": "claude_json"},                  # frequent but tiny side calls
    ]
    recompute_totals(s)
    ledger.ingest(db, [s])
    hist = _history(tmp_path, [("exact", "/p", T0, "hi"), ("old-apr", "/p", april + DAY, "x"),
                               ("old-jun", "/p", T0 - 100 * DAY, "y")])
    backfill.run(db, history_path=hist, claude_json_path=None)
    by_id = _by_id(db)
    assert by_id["old-apr"].turns_raw[0]["model"] == "claude-sonnet-4-6"
    # June has no exact data: nearest month with data wins (April is ~60 days away,
    # September 100 days away)
    assert by_id["old-jun"].turns_raw[0]["model"] == "claude-sonnet-4-6"
