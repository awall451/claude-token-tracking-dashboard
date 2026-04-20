#!/usr/bin/env python3
"""
Parse ~/.claude/projects/ session JSONL files and emit stats.json.

Usage:
    python3 parser/parse.py [--out stats.json] [--pretty]
"""

import argparse
import json
import pathlib
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone

from models import SessionStats, aggregate_by_day, aggregate_by_project, caveman_comparison, rolling_windows

CLAUDE_DIR = pathlib.Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"
HISTORY_FILE = CLAUDE_DIR / "history.jsonl"

# Detect caveman activation in user message content
_CAVEMAN_RE = re.compile(r'<command-name>/caveman|caveman:caveman', re.IGNORECASE)
_CAVEMAN_MODE_RE = re.compile(r'<command-args>(ultra|lite|full|wenyan[^\s<]*)', re.IGNORECASE)


def decode_project_path(encoded: str) -> str:
    """Convert encoded dir name back to filesystem path."""
    # '-home-dillon-lab-foo' → '/home/dillon/lab/foo'
    # Edge: double hyphens in real paths are ambiguous; best-effort only
    return encoded.replace("-", "/", 1) if encoded.startswith("-") else encoded


def load_history() -> dict[str, str]:
    """Returns {sessionId: project_path}."""
    mapping = {}
    if not HISTORY_FILE.exists():
        return mapping
    for line in HISTORY_FILE.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            sid = obj.get("sessionId")
            proj = obj.get("project", "")
            if sid and proj:
                mapping[sid] = proj
        except json.JSONDecodeError:
            continue
    return mapping


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


def parse_session_file(jsonl_path: pathlib.Path, history_map: dict[str, str]) -> SessionStats | None:
    """Parse one session JSONL file into a SessionStats."""
    seen_request_ids: set[str] = set()
    session_id = jsonl_path.stem
    project_path = history_map.get(session_id, "")

    stats = SessionStats(session_id=session_id, project_path=project_path)
    turns_raw: list[dict] = []  # per-turn records for rolling window calc
    caveman_detected = False
    caveman_mode = ""

    lines = jsonl_path.read_text(errors="replace").splitlines()
    if not lines:
        return None

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        entry_type = obj.get("type", "")
        ts = obj.get("timestamp", 0)
        if isinstance(ts, str):
            try:
                ts = int(ts)
            except (ValueError, TypeError):
                try:
                    from datetime import datetime, timezone
                    ts = int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)
                except Exception:
                    ts = 0

        if ts:
            if not stats.start_ts or ts < stats.start_ts:
                stats.start_ts = ts
            if ts > stats.end_ts:
                stats.end_ts = ts

        if not project_path and obj.get("cwd"):
            stats.project_path = obj["cwd"]

        if entry_type == "user":
            text = _text_content(obj.get("message", {}))
            if _CAVEMAN_RE.search(text):
                caveman_detected = True
                m = _CAVEMAN_MODE_RE.search(text)
                if m:
                    caveman_mode = m.group(1).lower()
                elif not caveman_mode:
                    caveman_mode = "full"

        elif entry_type == "assistant":
            request_id = obj.get("requestId", "")
            if request_id and request_id in seen_request_ids:
                continue  # duplicate streaming entry
            if request_id:
                seen_request_ids.add(request_id)

            msg = obj.get("message", {})
            usage = msg.get("usage", {})

            if not usage:
                continue

            model = msg.get("model", "unknown")
            inp = usage.get("input_tokens", 0)
            cc = usage.get("cache_creation_input_tokens", 0)
            cr = usage.get("cache_read_input_tokens", 0)
            out = usage.get("output_tokens", 0)
            stats.models[model] += 1
            stats.turns += 1
            stats.input_tokens += inp
            stats.cache_creation_tokens += cc
            stats.cache_read_tokens += cr
            stats.output_tokens += out
            turns_raw.append({
                "ts": ts,
                "input": inp,
                "cache_creation": cc,
                "cache_read": cr,
                "output": out,
                "cost": inp + cc + out,
            })

    if stats.turns == 0:
        return None

    stats.caveman = caveman_detected
    stats.caveman_mode = caveman_mode
    stats.turns_raw = turns_raw
    return stats


def parse_all() -> list[SessionStats]:
    if not PROJECTS_DIR.exists():
        print(f"ERROR: {PROJECTS_DIR} not found", file=sys.stderr)
        sys.exit(1)

    history_map = load_history()
    sessions = []

    for project_dir in sorted(PROJECTS_DIR.iterdir()):
        if not project_dir.is_dir():
            continue
        fallback_path = decode_project_path(project_dir.name)
        for jsonl_file in sorted(project_dir.glob("*.jsonl")):
            stats = parse_session_file(jsonl_file, history_map)
            if stats is None:
                continue
            if not stats.project_path:
                stats.project_path = fallback_path
            sessions.append(stats)

    return sessions


def build_output(sessions: list[SessionStats]) -> dict:
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

    return {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "rolling": rolling_windows(all_turns),
        "totals": {
            "sessions": len(sessions),
            "turns": total_turns,
            "input_tokens": total_input,
            "cache_creation_tokens": total_cache_creation,
            "cache_read_tokens": total_cache_read,
            "output_tokens": total_output,
            "total_cost_tokens": total_cost,
            "cache_hit_rate": round(total_cache_read / cache_total, 4) if cache_total else 0.0,
        },
        "caveman_comparison": caveman_comparison(sessions),
        "by_day": aggregate_by_day(sessions),
        "by_project": aggregate_by_project(sessions),
        "sessions": [s.to_dict() for s in sorted(sessions, key=lambda x: x.start_ts)],
    }


def anonymize_output(output: dict) -> dict:
    """Replace real project paths with generic labels (project-1, project-2, ...)."""
    import copy
    out = copy.deepcopy(output)
    # Build stable mapping from real path → label (sorted for reproducibility)
    paths = sorted(out["by_project"].keys())
    mapping = {p: f"project-{i+1}" for i, p in enumerate(paths)}

    out["by_project"] = {mapping.get(k, k): v for k, v in out["by_project"].items()}
    for s in out["sessions"]:
        s["project_path"] = mapping.get(s["project_path"], s["project_path"])
    return out


def main():
    parser = argparse.ArgumentParser(description="Parse Claude session data → stats.json")
    parser.add_argument("--out", default="stats.json", help="Output file (default: stats.json)")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON")
    parser.add_argument("--summary", action="store_true", help="Print summary to stdout")
    parser.add_argument("--anonymize", action="store_true", help="Replace project paths with generic labels")
    args = parser.parse_args()

    sessions = parse_all()
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
