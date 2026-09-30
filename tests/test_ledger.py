import json

from parser import ledger, parse


def _write_jsonl(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


def _user(text, ts):
    return {"type": "user", "timestamp": ts, "cwd": "/home/u/work", "message": {"role": "user", "content": text}}


def _assistant(rid, ts, content=(), output=10, **usage_extra):
    usage = {"input_tokens": 1, "cache_creation_input_tokens": 100,
             "cache_read_input_tokens": 1000, "output_tokens": output}
    usage.update(usage_extra)
    return {"type": "assistant", "requestId": rid, "timestamp": ts, "cwd": "/home/u/work",
            "message": {"model": "claude-opus-5-5", "content": list(content), "usage": usage}}


EDIT = {"type": "tool_use", "name": "Edit", "input": {
    "file_path": "/home/u/work/a.py", "old_string": "x\ny", "new_string": "x\ny\nz\nw"}}
WRITE = {"type": "tool_use", "name": "Write", "input": {"file_path": "/home/u/work/b.py", "content": "1\n2\n3\n"}}


def _projects(tmp_path, rid_outputs=(("r1", 10),), session="s1"):
    projects = tmp_path / "projects"
    entries = [_user("go", "2026-09-29T10:00:00Z")]
    for i, (rid, out) in enumerate(rid_outputs):
        entries.append(_assistant(rid, f"2026-09-29T10:0{i + 1}:00Z", [EDIT, WRITE], output=out,
                                  cache_creation={"ephemeral_5m_input_tokens": 40, "ephemeral_1h_input_tokens": 60}))
    _write_jsonl(projects / "-home-u-work" / f"{session}.jsonl", entries)
    return projects


def _parse(projects):
    return parse.parse_all(projects_dir=projects, history_map={}, name_map={})


def test_turn_records_tool_calls_and_lines(tmp_path):
    s = _parse(_projects(tmp_path))[0]
    t = s.turns_raw[0]
    assert t["rid"] == "r1"
    assert t["tool_calls"] == 2
    assert t["lines_added"] == 2 + 3    # Edit grew 2 → 4 lines; Write added 3
    assert t["lines_removed"] == 0
    assert t["cache_write_1h"] == 60


def test_ingest_then_load_round_trips(tmp_path):
    db = ledger.connect(tmp_path / "l.sqlite")
    ledger.ingest(db, _parse(_projects(tmp_path, [("r1", 10), ("r2", 20)])))
    loaded = ledger.load_sessions(db)
    assert len(loaded) == 1
    s = loaded[0]
    assert s.turns == 2
    assert s.output_tokens == 30
    assert s.cache_creation_1h_tokens == 120
    assert s.turns_raw[1]["paths"] == ["/home/u/work/a.py", "/home/u/work/b.py"]


def test_ingest_is_idempotent_and_larger_usage_wins(tmp_path):
    db = ledger.connect(tmp_path / "l.sqlite")
    ledger.ingest(db, _parse(_projects(tmp_path, [("r1", 5)])))
    ledger.ingest(db, _parse(_projects(tmp_path, [("r1", 5)])))
    assert ledger.load_sessions(db)[0].output_tokens == 5
    ledger.ingest(db, _parse(_projects(tmp_path, [("r1", 50)])))
    assert ledger.load_sessions(db)[0].output_tokens == 50
    ledger.ingest(db, _parse(_projects(tmp_path, [("r1", 7)])))   # smaller partial never shrinks it
    assert ledger.load_sessions(db)[0].output_tokens == 50


def test_history_survives_transcript_deletion(tmp_path):
    db = ledger.connect(tmp_path / "l.sqlite")
    projects = _projects(tmp_path)
    ledger.ingest(db, _parse(projects))
    (projects / "-home-u-work" / "s1.jsonl").unlink()
    _write_jsonl(projects / "-home-u-work" / "s2.jsonl", [_assistant("x1", "2026-09-30T10:00:00Z")])
    ledger.ingest(db, _parse(projects))
    assert sorted(s.session_id for s in ledger.load_sessions(db)) == ["s1", "s2"]


def test_session_metadata_persists(tmp_path):
    projects = tmp_path / "projects"
    _write_jsonl(projects / "-p" / "s1.jsonl", [
        {"type": "custom-title", "customTitle": "named", "sessionId": "s1"},
        _assistant("r1", "2026-09-29T10:00:00Z"),
        {"type": "cost-state", "sessionId": "s1", "totalCostUSD": 1.5, "modelUsage": {}},
    ])
    sub = projects / "-p" / "s1" / "subagents"
    _write_jsonl(sub / "agent-a.jsonl", [_assistant("ra", "2026-09-29T10:01:00Z")])
    (sub / "agent-a.meta.json").write_text(json.dumps({"agentType": "Explore", "description": "look"}))
    db = ledger.connect(tmp_path / "l.sqlite")
    ledger.ingest(db, parse.parse_all(projects_dir=projects, history_map={"s1": "/p"}, name_map={}))
    by_id = {s.session_id: s for s in ledger.load_sessions(db)}
    assert by_id["s1"].session_name == "named"
    assert by_id["s1"].project_path == "/p"
    assert by_id["s1"].reported["cost_usd"] == 1.5
    assert by_id["agent-a"].is_subagent and by_id["agent-a"].parent_session_id == "s1"
    assert by_id["agent-a"].agent_type == "Explore"


def test_parse_all_accepts_multiple_roots_without_double_counting(tmp_path):
    live = _projects(tmp_path / "live")
    archive = _projects(tmp_path / "archive")
    sessions = parse.parse_all(projects_dir=[live, archive], history_map={}, name_map={})
    assert [s.session_id for s in sessions] == ["s1"]
