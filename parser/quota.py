"""
Real subscription quota from statusline snapshots.

extras/statusline-tee.sh appends `{ts, session_id, rate_limits}` lines (epoch seconds)
to QUOTA_LOG whenever Claude Code refreshes the statusline on a Pro/Max plan.
This module turns that log into the latest 5-hour / 7-day usage and an implied
5-hour limit in cost tokens (tokens seen in the current window ÷ used %).
"""

import json
import os
import pathlib

QUOTA_LOG = pathlib.Path(os.environ.get(
    "QUOTA_LOG", str(pathlib.Path(os.environ.get("CLAUDE_DIR", pathlib.Path.home() / ".claude")) / "token-tracker" / "quota.jsonl")))

WINDOWS = {"five_hour": 5 * 3600, "seven_day": 7 * 24 * 3600}
# Below this the percentage is too coarse to extrapolate a limit from.
MIN_PCT_FOR_IMPLIED_LIMIT = 5
HISTORY_STEP_S = 900       # one history point per 15 min
HISTORY_SPAN_S = 8 * 24 * 3600


def _read(path: pathlib.Path) -> list[dict]:
    snaps = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("ts"), (int, float)) and isinstance(obj.get("rate_limits"), dict):
            snaps.append(obj)
    return sorted(snaps, key=lambda s: s["ts"])


def summarize(path: pathlib.Path, turns: list[dict], now: float) -> dict | None:
    """Latest usage per window, implied limits, and a downsampled history. None if no log."""
    try:
        snaps = _read(path)
    except FileNotFoundError:
        return None
    if not snaps:
        return None

    out: dict = {"age_seconds": int(now - snaps[-1]["ts"]), "history": []}
    for window, span in WINDOWS.items():
        latest = next((s for s in reversed(snaps) if window in s["rate_limits"]), None)
        if latest is None:
            continue
        w = latest["rate_limits"][window]
        pct = w.get("used_percentage") or 0
        resets_at = w.get("resets_at") or 0
        start_ms, end_ms = (resets_at - span) * 1000, latest["ts"] * 1000
        window_cost = sum(t["cost"] for t in turns if start_ms <= t.get("ts", 0) <= end_ms)
        implied = round(window_cost / (pct / 100)) if pct >= MIN_PCT_FOR_IMPLIED_LIMIT else None
        out[window] = {
            "used_percentage": pct,
            "resets_at": resets_at,
            "captured_at": latest["ts"],
            "window_cost_tokens": window_cost,
            "implied_limit_cost_tokens": implied,
        }

    last_point = None
    for s in snaps:
        if s["ts"] < now - HISTORY_SPAN_S:
            continue
        if last_point is not None and s["ts"] - last_point < HISTORY_STEP_S:
            continue
        rl = s["rate_limits"]
        out["history"].append({
            "ts": s["ts"],
            "five_hour": (rl.get("five_hour") or {}).get("used_percentage"),
            "seven_day": (rl.get("seven_day") or {}).get("used_percentage"),
        })
        last_point = s["ts"]
    return out
