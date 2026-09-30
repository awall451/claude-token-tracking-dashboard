import json

from parser import parse
from parser.models import SessionStats, aggregate_by_day


def _write_jsonl(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


def _assistant(request_id, ts, output, *, model="claude-opus-5-5", cwd="/home/u/proj", **usage_extra):
    usage = {
        "input_tokens": 10,
        "cache_creation_input_tokens": 100,
        "cache_read_input_tokens": 1000,
        "output_tokens": output,
    }
    usage.update(usage_extra)
    return {
        "type": "assistant",
        "requestId": request_id,
        "timestamp": ts,
        "cwd": cwd,
        "message": {"model": model, "usage": usage, "content": []},
    }


def test_dedup_keeps_final_streamed_usage(tmp_path):
    # Subagent transcripts log partial usage on the first streamed chunk.
    f = tmp_path / "s1.jsonl"
    _write_jsonl(f, [
        _assistant("req_1", "2026-09-29T10:00:00Z", 8),
        _assistant("req_1", "2026-09-29T10:00:01Z", 14182),
        _assistant("req_2", "2026-09-29T10:01:00Z", 50),
    ])
    s = parse.parse_session_file(f)
    assert s.turns == 2
    assert s.output_tokens == 14182 + 50
    assert s.input_tokens == 20


def test_iso_timestamps_parsed_to_ms(tmp_path):
    f = tmp_path / "s1.jsonl"
    _write_jsonl(f, [_assistant("req_1", "2026-09-29T10:00:00Z", 5)])
    s = parse.parse_session_file(f)
    assert s.start_ts == 1790676000000
    assert s.turns_raw[0]["ts"] == 1790676000000


def test_new_usage_fields_captured(tmp_path):
    f = tmp_path / "s1.jsonl"
    _write_jsonl(f, [_assistant(
        "req_1", "2026-09-29T10:00:00Z", 300,
        cache_creation={"ephemeral_5m_input_tokens": 40, "ephemeral_1h_input_tokens": 60},
        output_tokens_details={"thinking_tokens": 120},
        server_tool_use={"web_search_requests": 2, "web_fetch_requests": 1},
    )])
    s = parse.parse_session_file(f)
    assert s.cache_creation_5m_tokens == 40
    assert s.cache_creation_1h_tokens == 60
    assert s.thinking_tokens == 120
    assert s.web_search_requests == 2
    assert s.web_fetch_requests == 1


def test_iteration_models_counted(tmp_path):
    entry = _assistant("req_1", "2026-09-29T10:00:00Z", 5, iterations=[
        {"model": "claude-opus-5-5", "output_tokens": 3},
        {"model": "claude-fable-5-1", "output_tokens": 2},
    ])
    f = tmp_path / "s1.jsonl"
    _write_jsonl(f, [entry])
    s = parse.parse_session_file(f)
    assert dict(s.models) == {"claude-opus-5-5": 1, "claude-fable-5-1": 1}


def test_custom_title_read_in_same_pass(tmp_path):
    f = tmp_path / "s1.jsonl"
    _write_jsonl(f, [
        {"type": "custom-title", "customTitle": "my title", "sessionId": "s1"},
        _assistant("req_1", "2026-09-29T10:00:00Z", 5),
    ])
    s = parse.parse_session_file(f)
    assert s.custom_title == "my title"


def test_parse_all_discovers_subagents(tmp_path):
    projects = tmp_path / "projects"
    proj = projects / "-home-u-proj"
    _write_jsonl(proj / "sess1.jsonl", [_assistant("req_main", "2026-09-29T10:00:00Z", 10)])
    sub = proj / "sess1" / "subagents"
    _write_jsonl(sub / "agent-abc.jsonl", [_assistant("req_sub", "2026-09-29T10:02:00Z", 20)])
    (sub / "agent-abc.meta.json").write_text(json.dumps({
        "agentType": "general-purpose", "description": "Research water",
    }))

    sessions = parse.parse_all(projects_dir=projects, history_map={}, name_map={})
    by_id = {s.session_id: s for s in sessions}
    assert set(by_id) == {"sess1", "agent-abc"}
    child = by_id["agent-abc"]
    assert child.is_subagent
    assert child.parent_session_id == "sess1"
    assert child.agent_type == "general-purpose"
    assert child.session_name == "Research water"
    assert child.project_path == "/home/u/proj"


def test_parse_all_prefers_history_then_cwd_then_decoded_dir(tmp_path):
    projects = tmp_path / "projects"
    proj = projects / "-home-u-proj"
    _write_jsonl(proj / "a.jsonl", [_assistant("r1", "2026-09-29T10:00:00Z", 1, cwd="/from/cwd")])
    no_cwd = _assistant("r2", "2026-09-29T10:00:00Z", 1)
    del no_cwd["cwd"]
    _write_jsonl(proj / "b.jsonl", [no_cwd])
    _write_jsonl(proj / "c.jsonl", [_assistant("r3", "2026-09-29T10:00:00Z", 1)])

    sessions = parse.parse_all(projects_dir=projects, history_map={"c": "/from/history"}, name_map={})
    paths = {s.session_id: s.project_path for s in sessions}
    assert paths == {"a": "/from/cwd", "b": "/home/u/proj", "c": "/from/history"}


def test_parse_all_cache_reuses_unchanged_files(tmp_path):
    projects = tmp_path / "projects"
    _write_jsonl(projects / "-p" / "a.jsonl", [_assistant("r1", "2026-09-29T10:00:00Z", 1)])
    cache = {}
    first = parse.parse_all(projects_dir=projects, history_map={}, name_map={}, cache=cache)
    second = parse.parse_all(projects_dir=projects, history_map={}, name_map={}, cache=cache)
    assert first[0] is second[0]


def test_decode_project_path_replaces_all_dashes():
    assert parse.decode_project_path("-home-dillon-lab-foo") == "/home/dillon/lab/foo"


def test_by_day_buckets_turns_by_turn_timestamp():
    day1 = 1790676000000            # 2026-09-29T10:00Z
    day2 = day1 + 24 * 3600 * 1000  # next day
    s = SessionStats(session_id="s", project_path="/p", turns=2, start_ts=day1, end_ts=day2,
                     input_tokens=2, output_tokens=20)
    s.turns_raw = [
        {"ts": day1, "input": 1, "cache_creation": 0, "cache_read": 0, "output": 5, "cost": 6},
        {"ts": day2, "input": 1, "cache_creation": 0, "cache_read": 0, "output": 15, "cost": 16},
    ]
    out = aggregate_by_day([s])
    assert out["2026-09-29"]["total_cost_tokens"] == 6
    assert out["2026-09-30"]["total_cost_tokens"] == 16
    assert out["2026-09-29"]["sessions"] == 1
    assert out["2026-09-30"]["sessions"] == 0


def test_anonymize_blanks_session_names():
    out = {
        "by_project": {"/home/u/secret": {}},
        "sessions": [{"project_path": "/home/u/secret", "session_name": "fix my bank login"}],
    }
    anon = parse.anonymize_output(out)
    assert anon["sessions"][0]["session_name"] == ""
    assert anon["sessions"][0]["project_path"] == "project-1"


def _cost_state(session_id, usd, fable_out=100):
    return {
        "type": "cost-state", "sessionId": session_id, "totalCostUSD": usd,
        "totalLinesAdded": 7, "totalLinesRemoved": 2,
        "modelUsage": {"claude-fable-5-1": {
            "inputTokens": 1, "outputTokens": fable_out, "thinkingTokens": 0,
            "cacheReadInputTokens": 10, "cacheCreationInputTokens": 5,
            "webSearchRequests": 0, "costUSD": usd,
        }},
    }


def test_cost_state_attached_to_session_including_orphaned_copies(tmp_path):
    projects = tmp_path / "projects"
    proj = projects / "-p"
    _write_jsonl(proj / "sess1.jsonl", [
        _assistant("r1", "2026-09-29T10:00:00Z", 1),
        _cost_state("sess1", 1.50),
    ])
    # Orphaned metadata copy with a later (larger) cumulative state and no assistant turns
    _write_jsonl(proj / "sess1.orphaned-123-abc.jsonl", [_cost_state("sess1", 2.25, fable_out=200)])

    sessions = parse.parse_all(projects_dir=projects, history_map={}, name_map={})
    assert [s.session_id for s in sessions] == ["sess1"]
    rep = sessions[0].reported
    assert rep["cost_usd"] == 2.25
    assert rep["lines_added"] == 7
    assert rep["model_usage"]["claude-fable-5-1"]["outputTokens"] == 200
    assert rep["total_tokens"] == 1 + 200 + 10 + 5


def test_build_output_totals_include_reported_cost(tmp_path):
    projects = tmp_path / "projects"
    _write_jsonl(projects / "-p" / "s.jsonl", [_assistant("r1", "2026-09-29T10:00:00Z", 1), _cost_state("s", 3.0)])
    sessions = parse.parse_all(projects_dir=projects, history_map={}, name_map={})
    out = parse.build_output(sessions)
    assert out["totals"]["reported_cost_usd"] == 3.0
    assert out["totals"]["reported_sessions"] == 1


def test_anonymize_covers_attributed_projects_and_tags():
    out = {
        "by_project": {"client-secret-app": {}},
        "by_launch_dir": {"/home/u/hub": {}},
        "by_tag": {"client": {"Secret Corp": {"turns": 1}}},
        "sessions": [{
            "project_path": "/home/u/hub", "session_name": "x",
            "projects": {"client-secret-app": 5},
            "turns_raw": [{"project": "client-secret-app", "tags": {"client": "Secret Corp"}}],
        }],
    }
    anon = parse.anonymize_output(out)
    blob = json.dumps(anon)
    assert "secret" not in blob.lower()
    assert anon["by_tag"] == {"client": {"client-1": {"turns": 1}}}
