import json

from parser import attribution, parse
from parser.models import aggregate_by_tag, aggregate_by_turn_project

HOME = "/home/u"

CONFIG = attribution.load_config_text("""
[[hub]]
path = "~/work"
tags = { client = "acme" }

[[project]]
path = "~/work/site"
name = "acme-site"
tags = { client = "acme", kind = "web" }

[[project]]
path = "~/solo"
tags = { kind = "tool" }

[skills]
deep-research = { kind = "research" }
""", home=HOME)


def test_resolve_explicit_project_longest_prefix():
    assert attribution.resolve(f"{HOME}/work/site/src/app.js", CONFIG) == ("acme-site", {"client": "acme", "kind": "web"})


def test_resolve_hub_first_segment():
    assert attribution.resolve(f"{HOME}/work/wards/README.md", CONFIG) == ("wards", {"client": "acme"})


def test_resolve_hub_root_itself():
    assert attribution.resolve(f"{HOME}/work", CONFIG) == ("work", {"client": "acme"})


def test_resolve_project_without_name_uses_basename():
    assert attribution.resolve(f"{HOME}/solo/x.py", CONFIG) == ("solo", {"kind": "tool"})


def test_resolve_prefix_is_component_wise():
    assert attribution.resolve(f"{HOME}/worker/x", CONFIG) is None


def test_resolve_unmatched_is_none():
    assert attribution.resolve("/tmp/scratch/x", CONFIG) is None


def test_extract_paths_from_tool_inputs():
    blocks = [
        {"type": "tool_use", "name": "Edit", "input": {"file_path": f"{HOME}/work/site/a.js"}},
        {"type": "tool_use", "name": "Grep", "input": {"path": "src", "pattern": "x"}},
        {"type": "tool_use", "name": "Bash", "input": {"command": "cd ~/work/wards && make test"}},
        {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}},
        {"type": "text", "text": "hello"},
    ]
    paths = attribution.extract_paths(blocks, cwd=f"{HOME}/work", home=HOME)
    assert paths == [f"{HOME}/work/site/a.js", f"{HOME}/work/src", f"{HOME}/work/wards"]


def _turn(ts, exchange, paths=(), tag=None, cwd=f"{HOME}/work"):
    return {"ts": ts, "input": 1, "cache_creation": 0, "cache_read": 0, "output": 1, "cost": 2,
            "exchange": exchange, "paths": list(paths), "tag": tag, "cwd": cwd}


def test_attribute_exchange_majority_and_inheritance():
    turns = [
        _turn(1, 0, [f"{HOME}/work/wards/a"]),
        _turn(2, 0, [f"{HOME}/work/wards/b", f"{HOME}/work/site/c"]),
        _turn(3, 0),
        _turn(4, 1),                               # no signal → inherits wards
        _turn(5, 2, [f"{HOME}/work/site/d"]),
    ]
    attribution.attribute_turns(turns, session_project=f"{HOME}/work", config=CONFIG)
    assert [t["project"] for t in turns] == ["wards", "wards", "wards", "wards", "acme-site"]
    assert turns[4]["tags"] == {"client": "acme", "kind": "web"}


def test_attribute_first_exchange_without_signal_uses_session_project():
    turns = [_turn(1, 0)]
    attribution.attribute_turns(turns, session_project=f"{HOME}/work", config=CONFIG)
    assert turns[0]["project"] == "work"


def test_attribute_unconfigured_session_keeps_launch_path():
    turns = [_turn(1, 0, ["/elsewhere/x"])]
    attribution.attribute_turns(turns, session_project="/elsewhere", config=CONFIG)
    assert turns[0]["project"] == "/elsewhere"
    assert turns[0]["tags"] == {}


def test_manual_tag_overrides_from_that_exchange_on():
    turns = [
        _turn(1, 0, [f"{HOME}/work/wards/a"]),
        _turn(2, 1, [f"{HOME}/work/wards/b"], tag="goodman-coffee"),
        _turn(3, 2, [f"{HOME}/work/site/c"]),
    ]
    attribution.attribute_turns(turns, session_project=f"{HOME}/work", config=CONFIG)
    assert [t["project"] for t in turns] == ["wards", "goodman-coffee", "goodman-coffee"]


def test_skill_tags_merged():
    turns = [_turn(1, 0, [f"{HOME}/work/wards/a"])]
    attribution.attribute_turns(turns, session_project=f"{HOME}/work", config=CONFIG, skill="deep-research")
    assert turns[0]["tags"] == {"client": "acme", "kind": "research"}


# --- integration through the parser -----------------------------------------

def _write_jsonl(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


def _user(text, ts):
    return {"type": "user", "timestamp": ts, "cwd": f"{HOME}/work", "message": {"role": "user", "content": text}}


def _tool_result(ts):
    return {"type": "user", "timestamp": ts, "cwd": f"{HOME}/work", "toolUseResult": {},
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "ok"}]}}


def _assistant(rid, ts, content, output=10):
    return {"type": "assistant", "requestId": rid, "timestamp": ts, "cwd": f"{HOME}/work",
            "message": {"model": "claude-opus-5-5", "content": content,
                        "usage": {"input_tokens": 1, "cache_creation_input_tokens": 0,
                                  "cache_read_input_tokens": 0, "output_tokens": output}}}


def test_parser_attributes_turns_and_emits_by_project(tmp_path):
    projects = tmp_path / "projects"
    edit = {"type": "tool_use", "name": "Edit", "input": {"file_path": f"{HOME}/work/wards/x.sh"}}
    _write_jsonl(projects / "-home-u-work" / "s1.jsonl", [
        _user("fix wards", "2026-09-29T10:00:00Z"),
        _assistant("r1", "2026-09-29T10:00:01Z", [edit], output=1),
        _assistant("r1", "2026-09-29T10:00:02Z", [{"type": "text", "text": "done"}], output=5),
        _tool_result("2026-09-29T10:00:03Z"),
        _assistant("r2", "2026-09-29T10:00:04Z", [{"type": "text", "text": "ok"}]),
        _user("<command-name>/tag</command-name>\n<command-args>acme-site</command-args>", "2026-09-29T10:01:00Z"),
        _assistant("r3", "2026-09-29T10:01:01Z", [{"type": "text", "text": "ok"}]),
    ])
    sessions = parse.parse_all(projects_dir=projects, history_map={"s1": f"{HOME}/work"}, name_map={})
    attribution.attribute_sessions(sessions, CONFIG)
    s = sessions[0]
    assert [t["project"] for t in s.turns_raw] == ["wards", "wards", "acme-site"]

    by_proj = aggregate_by_turn_project(sessions)
    assert by_proj["wards"]["turns"] == 2
    assert by_proj["wards"]["output_tokens"] == 15   # max-output entry of r1 (5) + r2 (10)
    assert by_proj["acme-site"]["sessions"] == 1

    by_tag = aggregate_by_tag(sessions)
    assert by_tag["client"]["acme"]["turns"] == 3   # /tag of a configured project picks up its tags
    assert by_tag["kind"]["web"]["turns"] == 1

    d = s.to_dict()
    assert "paths" not in d["turns_raw"][0]
    assert d["projects"] == {"wards": 17, "acme-site": 11}


def test_subagent_inherits_parent_project_at_spawn(tmp_path):
    projects = tmp_path / "projects"
    edit = {"type": "tool_use", "name": "Edit", "input": {"file_path": f"{HOME}/work/wards/x.sh"}}
    _write_jsonl(projects / "-home-u-work" / "s1.jsonl", [
        _user("go", "2026-09-29T10:00:00Z"),
        _assistant("r1", "2026-09-29T10:00:01Z", [edit]),
    ])
    sub_entry = _assistant("rs", "2026-09-29T10:05:00Z", [{"type": "text", "text": "researching"}])
    sub_entry["attributionSkill"] = "deep-research"
    _write_jsonl(projects / "-home-u-work" / "s1" / "subagents" / "agent-a.jsonl", [
        _user("research", "2026-09-29T10:04:59Z"), sub_entry,
    ])
    sessions = parse.parse_all(projects_dir=projects, history_map={"s1": f"{HOME}/work"}, name_map={})
    attribution.attribute_sessions(sessions, CONFIG)
    child = next(s for s in sessions if s.is_subagent)
    assert child.turns_raw[0]["project"] == "wards"
    assert child.turns_raw[0]["tags"] == {"client": "acme", "kind": "research"}
