#!/usr/bin/env python3
"""
Parse ~/.claude/projects/ session JSONL files and emit stats.json.

Usage:
    python3 parser/parse.py [--out stats.json] [--pretty]
"""

import argparse
import difflib
import json
import os
import pathlib
import re
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime

try:
    from . import energy, quota
    from .attribution import attribute_sessions, extract_paths, load_config
    from .models import (
        SessionStats,
        aggregate_by_day,
        aggregate_by_project,
        aggregate_by_tag,
        aggregate_by_turn_project,
        caveman_comparison,
        lifetime_stats,
        recompute_totals,
        rolling_windows,
    )
except ImportError:  # run as a script / imported via sys.path (server/serve.py)
    import energy
    import quota
    from attribution import attribute_sessions, extract_paths, load_config
    from models import (
        SessionStats,
        aggregate_by_day,
        aggregate_by_project,
        aggregate_by_tag,
        aggregate_by_turn_project,
        caveman_comparison,
        lifetime_stats,
        recompute_totals,
        rolling_windows,
    )

CLAUDE_DIR = pathlib.Path(os.environ.get("CLAUDE_DIR", str(pathlib.Path.home() / ".claude")))
PROJECTS_DIR = CLAUDE_DIR / "projects"
HISTORY_FILE = CLAUDE_DIR / "history.jsonl"
# ~/.claude.json sits one level above CLAUDE_DIR (e.g. /data/.claude → /data/.claude.json)
CLAUDE_JSON = CLAUDE_DIR.parent / (CLAUDE_DIR.name + ".json")

PLAN_LIMITS = {
    "standard": 600_000,
    "ultraplan_5x": 3_000_000,
    "ultraplan_20x": 12_000_000,
}

# Detect caveman activation in user message content or hook attachments
_CAVEMAN_RE = re.compile(r'<command-name>/caveman|caveman:caveman', re.IGNORECASE)
_CAVEMAN_MODE_RE = re.compile(r'<command-args>(ultra|lite|full|wenyan[^\s<]*)', re.IGNORECASE)
# Hook-based activation: "CAVEMAN MODE ACTIVE — level: ultra" or "CAVEMAN MODE ACTIVE (ultra)"
_CAVEMAN_HOOK_RE = re.compile(r'CAVEMAN MODE ACTIVE', re.IGNORECASE)
_TAG_RE = re.compile(r'<command-name>/tag</command-name>\s*<command-args>\s*([^<\s]+)')
_CAVEMAN_HOOK_MODE_RE = re.compile(r'level:\s*(ultra|lite|full|wenyan\S*)|CAVEMAN MODE ACTIVE\s*\((ultra|lite|full|wenyan\S*)\)', re.IGNORECASE)


def _detect_caveman_plugin(claude_dir: pathlib.Path) -> bool:
    """Return True if the caveman plugin is installed/enabled."""
    try:
        settings_path = claude_dir / "settings.json"
        if settings_path.exists():
            settings = json.loads(settings_path.read_text())
            if any("caveman" in k for k in settings.get("enabledPlugins", {})):
                return True
    except Exception:
        pass
    return (claude_dir / "plugins" / "marketplaces" / "caveman").is_dir()


def detect_plan() -> dict:
    """Detect plan tier from ~/.claude.json GrowthBook feature flags."""
    try:
        data = json.loads(CLAUDE_JSON.read_text())
        features = data.get("cachedGrowthBookFeatures", {})
        ultraplan = features.get("tengu_ultraplan_config", {})
        enabled = ultraplan.get("enabled", False) if isinstance(ultraplan, dict) else bool(ultraplan)
        return {"ultraplan": enabled, "detected": True}
    except Exception:
        return {"ultraplan": False, "detected": False}


def decode_project_path(encoded: str) -> str:
    """Convert encoded dir name back to filesystem path (best-effort).

    '-home-dillon-lab-foo' → '/home/dillon/lab/foo'. Real hyphens and dots are
    lossy in the encoding, so history.jsonl / entry cwd are preferred when present.
    """
    return encoded.replace("-", "/")


def _read_history() -> tuple[dict[str, str], dict[str, str]]:
    """Returns ({sessionId: project_path}, {sessionId: first prompt display})."""
    projects: dict[str, str] = {}
    names: dict[str, str] = {}
    if not HISTORY_FILE.exists():
        return projects, names
    for line in HISTORY_FILE.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        sid = obj.get("sessionId")
        if not sid:
            continue
        proj = obj.get("project", "")
        if proj:
            projects[sid] = proj
        display = obj.get("display", "")
        if display and display != "init" and sid not in names:
            names[sid] = display
    return projects, names


def load_history() -> dict[str, str]:
    """Returns {sessionId: project_path}."""
    return _read_history()[0]


def load_session_names() -> dict[str, str]:
    """Returns {sessionId: first prompt display}. /rename titles are read per file in parse_session_file."""
    return _read_history()[1]


def _text_content(message: dict) -> str:
    """Extract plain text from a message object (str or list of content blocks)."""
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text", ""))
        return "\n".join(parts)
    return ""


def _tool_stats(blocks: list) -> tuple[int, int, int]:
    """(tool calls, lines added, lines removed) for one assistant entry's tool_use blocks.

    Edit: line diff of old_string → new_string; Write: every content line counts as added
    (overwrites of existing files are over-counted — no before-image in the transcript).
    """
    calls = added = removed = 0
    for b in blocks:
        if not isinstance(b, dict) or b.get("type") != "tool_use":
            continue
        calls += 1
        inp = b.get("input") or {}
        if b.get("name") == "Edit":
            old = str(inp.get("old_string", "")).splitlines()
            new = str(inp.get("new_string", "")).splitlines()
            for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
                if tag in ("replace", "delete"):
                    removed += i2 - i1
                if tag in ("replace", "insert"):
                    added += j2 - j1
        elif b.get("name") == "Write":
            added += len(str(inp.get("content", "")).splitlines())
    return calls, added, removed


def _to_ms(ts) -> int:
    if isinstance(ts, (int, float)):
        return int(ts)
    if isinstance(ts, str) and ts:
        try:
            return int(ts)
        except ValueError:
            try:
                return int(datetime.fromisoformat(ts).timestamp() * 1000)
            except ValueError:
                return 0
    return 0


def parse_session_file(jsonl_path: pathlib.Path) -> SessionStats | None:
    """Parse one session JSONL file into a SessionStats.

    Independent of history.jsonl so results can be cached per file; the caller
    resolves project_path/session_name from `cwd`/`custom_title` and history.
    """
    lines = jsonl_path.read_text(errors="replace").splitlines()
    if not lines:
        return None

    stats = SessionStats(session_id=jsonl_path.stem, project_path="")
    caveman_detected = False
    caveman_mode = ""
    # requestId → {ts, msg, paths, exchange, tag, cwd}. Streaming writes several entries per
    # request (one per content block); subagent transcripts log partial usage on early chunks,
    # so keep the usage with the most output and collect tool paths across all entries.
    requests: dict[str, dict] = {}
    exchange = -1        # index of the user prompt the following turns answer
    pending_tag = None   # /tag seen in the current exchange

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        entry_type = obj.get("type", "")
        ts = _to_ms(obj.get("timestamp", 0))

        if ts:
            if not stats.start_ts or ts < stats.start_ts:
                stats.start_ts = ts
            if ts > stats.end_ts:
                stats.end_ts = ts

        if not stats.cwd and obj.get("cwd"):
            stats.cwd = obj["cwd"]

        if entry_type == "cost-state":
            sid = obj.get("sessionId") or stats.session_id
            prev = stats.cost_states.get(sid)
            if prev is None or obj.get("totalCostUSD", 0) >= prev.get("totalCostUSD", 0):
                stats.cost_states[sid] = obj

        elif entry_type == "custom-title":
            if obj.get("customTitle"):
                stats.custom_title = obj["customTitle"]

        elif entry_type == "user":
            message = obj.get("message", {})
            content = message.get("content")
            is_tool_result = isinstance(content, list) and any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
            if not is_tool_result and not obj.get("isMeta"):
                exchange += 1
            text = _text_content(message)
            m = _TAG_RE.search(text)
            if m:
                pending_tag = (exchange, m.group(1))
            if _CAVEMAN_RE.search(text):
                caveman_detected = True
                m = _CAVEMAN_MODE_RE.search(text)
                if m:
                    caveman_mode = m.group(1).lower()
                elif not caveman_mode:
                    caveman_mode = "full"

        elif entry_type == "attachment":
            # Hook-injected caveman activation (hook_success / hook_additional_context)
            att = obj.get("attachment", {})
            att_type = att.get("type", "")
            if att_type in ("hook_success", "hook_additional_context"):
                content = att.get("content", "")
                if isinstance(content, list):
                    content = " ".join(str(c) for c in content)
                if _CAVEMAN_HOOK_RE.search(content):
                    caveman_detected = True
                    m = _CAVEMAN_HOOK_MODE_RE.search(content)
                    if m and not caveman_mode:
                        caveman_mode = (m.group(1) or m.group(2) or "full").lower()
                    elif not caveman_mode:
                        caveman_mode = "full"

        elif entry_type == "assistant":
            msg = obj.get("message", {})
            if not msg.get("usage"):
                continue
            if obj.get("attributionSkill") and not stats.attribution_skill:
                stats.attribution_skill = obj["attributionSkill"]
            key = obj.get("requestId") or obj.get("uuid") or f"line{len(requests)}"
            content = msg.get("content") if isinstance(msg.get("content"), list) else []
            paths = extract_paths(content, cwd=obj.get("cwd", ""))
            calls, added, removed = _tool_stats(content)
            req = requests.get(key)
            if req is None:
                tag = pending_tag[1] if pending_tag and pending_tag[0] == exchange else None
                requests[key] = {"ts": ts, "msg": msg, "paths": paths, "exchange": max(exchange, 0),
                                 "tag": tag, "cwd": obj.get("cwd", ""), "rid": key,
                                 "tool_calls": calls, "lines_added": added, "lines_removed": removed}
            else:
                req["paths"].extend(paths)
                req["tool_calls"] += calls
                req["lines_added"] += added
                req["lines_removed"] += removed
                if msg["usage"].get("output_tokens", 0) >= req["msg"]["usage"].get("output_tokens", 0):
                    req["msg"] = msg

    for req in sorted(requests.values(), key=lambda r: r["ts"]):
        msg = req["msg"]
        usage = msg["usage"]
        inp = usage.get("input_tokens", 0)
        cc = usage.get("cache_creation_input_tokens", 0)
        out = usage.get("output_tokens", 0)
        model = msg.get("model", "unknown")
        cc_detail = usage.get("cache_creation") or {}
        stu = usage.get("server_tool_use") or {}
        stats.turns_raw.append({
            "rid": req["rid"],
            "ts": req["ts"],
            "model": model,
            "models": [i.get("model") for i in usage.get("iterations") or [] if i.get("model")] or [model],
            "input": inp,
            "cache_creation": cc,
            "cache_write_5m": cc_detail.get("ephemeral_5m_input_tokens", 0),
            "cache_write_1h": cc_detail.get("ephemeral_1h_input_tokens", 0),
            "cache_read": usage.get("cache_read_input_tokens", 0),
            "output": out,
            "thinking": (usage.get("output_tokens_details") or {}).get("thinking_tokens", 0),
            "web_search": stu.get("web_search_requests", 0),
            "web_fetch": stu.get("web_fetch_requests", 0),
            "cost": inp + cc + out,
            "tool_calls": req["tool_calls"],
            "lines_added": req["lines_added"],
            "lines_removed": req["lines_removed"],
            "source": "transcript",
            "estimated": False,
            # attribution inputs; stripped from to_dict()
            "exchange": req["exchange"],
            "paths": req["paths"],
            "tag": req["tag"],
            "cwd": req["cwd"],
        })
    recompute_totals(stats)

    if stats.turns == 0 and not stats.cost_states:
        return None

    stats.caveman = caveman_detected
    stats.caveman_mode = caveman_mode
    return stats


def _reported_from_cost_state(cs: dict) -> dict:
    model_usage = cs.get("modelUsage") or {}
    total = 0
    for u in model_usage.values():
        total += sum(u.get(k, 0) for k in
                     ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens"))
    return {
        "cost_usd": round(cs.get("totalCostUSD", 0.0), 4),
        "total_tokens": total,
        "lines_added": cs.get("totalLinesAdded", 0),
        "lines_removed": cs.get("totalLinesRemoved", 0),
        "model_usage": model_usage,
    }


def _parse_cached(jsonl_file: pathlib.Path, cache: dict | None) -> SessionStats | None:
    if cache is None:
        return parse_session_file(jsonl_file)
    st = jsonl_file.stat()
    key = (st.st_mtime_ns, st.st_size)
    hit = cache.get(jsonl_file)
    if hit and hit[0] == key:
        return hit[1]
    stats = parse_session_file(jsonl_file)
    cache[jsonl_file] = (key, stats)
    return stats


def _session_files(project_dir: pathlib.Path):
    """Yield (jsonl_file, parent_session_id or None) for main sessions and their subagents."""
    for jsonl_file in sorted(project_dir.glob("*.jsonl")):
        yield jsonl_file, None
    for jsonl_file in sorted(project_dir.glob("*/subagents/agent-*.jsonl")):
        yield jsonl_file, jsonl_file.parent.parent.name


def _read_agent_meta(jsonl_file: pathlib.Path) -> dict:
    meta = jsonl_file.with_suffix(".meta.json")
    try:
        return json.loads(meta.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _discover(roots: list[pathlib.Path]):
    """Yield (project_dir_name, jsonl_file, parent_id). A session present under several roots
    (live ~/.claude/projects + an archive copy) is read from the first root only."""
    seen = set()
    for root in roots:
        if not root.exists():
            continue
        for project_dir in sorted(root.iterdir()):
            if not project_dir.is_dir():
                continue
            for jsonl_file, parent_id in _session_files(project_dir):
                key = (project_dir.name, str(jsonl_file.relative_to(project_dir)))
                if key in seen:
                    continue
                seen.add(key)
                yield project_dir.name, jsonl_file, parent_id


def parse_all(projects_dir: pathlib.Path | list[pathlib.Path] | None = None,
              history_map: dict[str, str] | None = None,
              name_map: dict[str, str] | None = None,
              cache: dict | None = None) -> list[SessionStats]:
    """Parse every session + subagent transcript under one or more roots.

    `cache` (optional, caller-owned dict) reuses results for files whose mtime/size
    are unchanged — the long-running server passes one to make refreshes cheap.
    """
    roots = projects_dir if isinstance(projects_dir, list) else [projects_dir or PROJECTS_DIR]
    if not any(r.exists() for r in roots):
        print(f"ERROR: none of {roots} found", file=sys.stderr)
        sys.exit(1)

    if history_map is None or name_map is None:
        hist_projects, hist_names = _read_history()
        history_map = hist_projects if history_map is None else history_map
        name_map = hist_names if name_map is None else name_map

    sessions = []
    cost_states: dict[str, dict] = {}
    for dir_name, jsonl_file, parent_id in _discover(roots):
        fallback_path = decode_project_path(dir_name)
        stats = _parse_cached(jsonl_file, cache)
        if stats is None:
            continue
        for sid, cs in stats.cost_states.items():
            prev = cost_states.get(sid)
            if prev is None or cs.get("totalCostUSD", 0) >= prev.get("totalCostUSD", 0):
                cost_states[sid] = cs
        if stats.turns == 0:
            continue  # metadata-only file (e.g. *.orphaned-*.jsonl)
        if parent_id:
            meta = _read_agent_meta(jsonl_file)
            stats.is_subagent = True
            stats.parent_session_id = parent_id
            stats.agent_type = meta.get("agentType", "")
            stats.session_name = stats.custom_title or meta.get("description", "")
            stats.project_path = history_map.get(parent_id) or stats.cwd or fallback_path
        else:
            stats.session_name = stats.custom_title or name_map.get(stats.session_id, "")
            stats.project_path = history_map.get(stats.session_id) or stats.cwd or fallback_path
        sessions.append(stats)

    for stats in sessions:
        cs = None if stats.is_subagent else cost_states.get(stats.session_id)
        stats.reported = _reported_from_cost_state(cs) if cs else {}

    return sessions


def _impact_block(sessions, by_day, by_project, by_tag, config) -> dict:
    """Energy/water/carbon ranges for the dashboard's Environmental Impact panel."""
    total = [0.0, 0.0, 0.0]
    for s in sessions:
        for i, x in enumerate(s.wh):
            total[i] += x
    last_30 = list(by_day.items())[-30:]
    last_30_wh = [sum(d["wh"][i] for _, d in last_30) for i in range(3)]

    def with_impact(groups):
        return {k: energy.impact(v["wh"], config) for k, v in groups.items()}

    return {
        "total": energy.impact(total, config),
        "last_30d": energy.impact(last_30_wh, config),
        "by_project": with_impact(by_project),
        "by_tag": {k: with_impact(v) for k, v in by_tag.items()},
        "by_day": {day: round(d["wh"][1], 3) for day, d in by_day.items()},
        "assumptions": energy.config_summary(config),
    }


def build_output(sessions: list[SessionStats], tags_config=None, energy_config=None) -> dict:
    attribute_sessions(sessions, tags_config if tags_config is not None else load_config())
    energy_config = energy_config or energy.load_config()
    energy.annotate_sessions(sessions, energy_config)
    total_input = sum(s.input_tokens for s in sessions)
    total_cache_creation = sum(s.cache_creation_tokens for s in sessions)
    total_cache_read = sum(s.cache_read_tokens for s in sessions)
    total_output = sum(s.output_tokens for s in sessions)
    total_cost = sum(s.total_cost_tokens for s in sessions)
    total_turns = sum(s.turns for s in sessions)
    cache_total = total_cache_creation + total_cache_read

    all_turns = []
    for s in sessions:
        all_turns.extend(getattr(s, 'turns_raw', []))

    plan_info = detect_plan()
    if plan_info["ultraplan"]:
        plan_info["suggested_limit"] = PLAN_LIMITS["ultraplan_5x"]
        plan_info["limits"] = PLAN_LIMITS
    else:
        plan_info["suggested_limit"] = PLAN_LIMITS["standard"]
        plan_info["limits"] = PLAN_LIMITS

    quota_info = quota.summarize(quota.QUOTA_LOG, all_turns, time.time())
    implied_5h = ((quota_info or {}).get("five_hour") or {}).get("implied_limit_cost_tokens")
    if implied_5h:
        plan_info["suggested_limit"] = implied_5h
        plan_info["limit_source"] = "statusline"

    by_day = aggregate_by_day(sessions)
    by_project = aggregate_by_turn_project(sessions)
    by_tag = aggregate_by_tag(sessions)

    return {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "plan_info": plan_info,
        "rolling": rolling_windows(all_turns),
        "quota": quota_info,
        "totals": {
            "sessions": len(sessions),
            "turns": total_turns,
            "input_tokens": total_input,
            "cache_creation_tokens": total_cache_creation,
            "cache_read_tokens": total_cache_read,
            "output_tokens": total_output,
            "total_cost_tokens": total_cost,
            "cache_hit_rate": round(total_cache_read / cache_total, 4) if cache_total else 0.0,
            # Claude Code's own accounting where available (API-equivalent, not your plan bill)
            "reported_cost_usd": round(sum(s.reported.get("cost_usd", 0) for s in sessions), 2),
            "reported_tokens": sum(s.reported.get("total_tokens", 0) for s in sessions),
            "reported_sessions": sum(1 for s in sessions if s.reported),
        },
        "lifetime": lifetime_stats(sessions, by_day),
        "has_caveman_plugin": _detect_caveman_plugin(CLAUDE_DIR),
        "caveman_comparison": caveman_comparison(sessions),
        "by_day": by_day,
        # Per-turn attribution (hubs/projects/tags from tags.toml); by_launch_dir = old per-session view
        "by_project": by_project,
        "by_launch_dir": aggregate_by_project(sessions),
        "by_tag": by_tag,
        "impact": _impact_block(sessions, by_day, by_project, by_tag, energy_config),
        "sessions": [s.to_dict() for s in sorted(sessions, key=lambda x: x.start_ts)],
    }


def anonymize_output(output: dict) -> dict:
    """Replace project paths/names, tag values and session names with generic labels."""
    import copy
    out = copy.deepcopy(output)
    # Stable mapping (sorted for reproducibility) over every project identifier that appears
    names = set(out.get("by_project", {})) | set(out.get("by_launch_dir", {}))
    for s in out["sessions"]:
        names.add(s["project_path"])
        names.update(s.get("projects", {}))
    mapping = {p: f"project-{i+1}" for i, p in enumerate(sorted(names))}

    for key in ("by_project", "by_launch_dir"):
        if key in out:
            out[key] = {mapping.get(k, k): v for k, v in out[key].items()}
    def anon_tags(by_tag):
        return {
            tag_key: {f"{tag_key}-{i+1}": v for i, (_, v) in enumerate(sorted(values.items()))}
            for tag_key, values in by_tag.items()
        }

    out["by_tag"] = anon_tags(out.get("by_tag", {}))
    if "impact" in out:
        out["impact"]["by_project"] = {mapping.get(k, k): v for k, v in out["impact"]["by_project"].items()}
        out["impact"]["by_tag"] = anon_tags(out["impact"]["by_tag"])
    for s in out["sessions"]:
        s["project_path"] = mapping.get(s["project_path"], s["project_path"])
        s["session_name"] = ""  # usually the first prompt text — never safe to share
        s["projects"] = {mapping.get(k, k): v for k, v in s.get("projects", {}).items()}
        for t in s.get("turns_raw", []):
            if "project" in t:
                t["project"] = mapping.get(t["project"], t["project"])
            t.pop("tags", None)
    return out


def main():
    parser = argparse.ArgumentParser(description="Parse Claude session data → stats.json")
    parser.add_argument("--out", default="stats.json", help="Output file (default: stats.json)")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    parser.add_argument("--summary", action="store_true", help="Print summary to stdout")
    parser.add_argument("--anonymize", action="store_true", help="Replace project paths with generic labels")
    parser.add_argument("--ledger", help="SQLite ledger to upsert into and build from (keeps history past transcript cleanup)")
    parser.add_argument("--archive", action="append", default=[], help="Extra transcript root (repeatable)")
    args = parser.parse_args()

    roots = [PROJECTS_DIR, *(pathlib.Path(a) for a in args.archive)]
    sessions = parse_all(projects_dir=roots)
    if args.ledger:
        try:
            from . import ledger
        except ImportError:
            import ledger
        db = ledger.connect(pathlib.Path(args.ledger))
        ledger.ingest(db, sessions)
        sessions = ledger.load_sessions(db)
    output = build_output(sessions)
    if args.anonymize:
        output = anonymize_output(output)

    indent = 2 if args.pretty else None
    out_path = pathlib.Path(args.out)
    out_path.write_text(json.dumps(output, indent=indent))
    print(f"Wrote {out_path} ({out_path.stat().st_size} bytes, {len(sessions)} sessions)")

    if args.summary:
        t = output["totals"]
        cc = output["caveman_comparison"]
        print(f"\n=== Totals ===")
        print(f"  Sessions : {t['sessions']}")
        print(f"  Turns    : {t['turns']}")
        print(f"  Cost tok : {t['total_cost_tokens']:,}")
        print(f"  Cache hit: {t['cache_hit_rate']*100:.1f}%")
        print(f"\n=== Caveman vs Normal ===")
        print(f"  Caveman  : {cc['caveman']['sessions']} sessions, {cc['caveman']['avg_tokens_per_turn']} avg tok/turn")
        print(f"  Normal   : {cc['normal']['sessions']} sessions, {cc['normal']['avg_tokens_per_turn']} avg tok/turn")
        print(f"  Savings  : {cc['savings_pct']}%")


if __name__ == "__main__":
    main()
