from dataclasses import dataclass, field
from collections import defaultdict
from datetime import datetime, timezone
import time


@dataclass
class SessionStats:
    session_id: str
    project_path: str
    caveman: bool = False
    caveman_mode: str = ""  # lite/full/ultra/wenyan-* or ""
    turns: int = 0
    input_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    models: dict = field(default_factory=lambda: defaultdict(int))
    start_ts: int = 0
    end_ts: int = 0
    turns_raw: list = field(default_factory=list, repr=False)

    @property
    def total_cost_tokens(self):
        """Tokens that count against quota (cache reads are cheaper but still count)."""
        return self.input_tokens + self.cache_creation_tokens + self.output_tokens

    @property
    def total_tokens(self):
        return self.input_tokens + self.cache_creation_tokens + self.cache_read_tokens + self.output_tokens

    @property
    def cache_hit_rate(self):
        reads = self.cache_read_tokens
        writes = self.cache_creation_tokens
        total = reads + writes
        return round(reads / total, 4) if total > 0 else 0.0

    @property
    def avg_tokens_per_turn(self):
        return round(self.total_cost_tokens / self.turns, 1) if self.turns > 0 else 0.0

    def to_dict(self):
        return {
            "session_id": self.session_id,
            "project_path": self.project_path,
            "caveman": self.caveman,
            "caveman_mode": self.caveman_mode,
            "turns": self.turns,
            "input_tokens": self.input_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "output_tokens": self.output_tokens,
            "total_cost_tokens": self.total_cost_tokens,
            "total_tokens": self.total_tokens,
            "cache_hit_rate": self.cache_hit_rate,
            "avg_tokens_per_turn": self.avg_tokens_per_turn,
            "models": dict(self.models),
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
        }


def aggregate_by_day(sessions: list[SessionStats]) -> dict:
    by_day = defaultdict(lambda: {
        "input_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "output_tokens": 0,
        "total_cost_tokens": 0,
        "turns": 0,
        "sessions": 0,
        "caveman_sessions": 0,
    })
    for s in sessions:
        if not s.start_ts:
            continue
        from datetime import datetime, timezone
        day = datetime.fromtimestamp(s.start_ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        d = by_day[day]
        d["input_tokens"] += s.input_tokens
        d["cache_creation_tokens"] += s.cache_creation_tokens
        d["cache_read_tokens"] += s.cache_read_tokens
        d["output_tokens"] += s.output_tokens
        d["total_cost_tokens"] += s.total_cost_tokens
        d["turns"] += s.turns
        d["sessions"] += 1
        if s.caveman:
            d["caveman_sessions"] += 1
    return dict(sorted(by_day.items()))


def aggregate_by_project(sessions: list[SessionStats]) -> dict:
    by_proj = defaultdict(lambda: {
        "input_tokens": 0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "output_tokens": 0,
        "total_cost_tokens": 0,
        "turns": 0,
        "sessions": 0,
        "caveman_sessions": 0,
    })
    for s in sessions:
        p = by_proj[s.project_path or "unknown"]
        p["input_tokens"] += s.input_tokens
        p["cache_creation_tokens"] += s.cache_creation_tokens
        p["cache_read_tokens"] += s.cache_read_tokens
        p["output_tokens"] += s.output_tokens
        p["total_cost_tokens"] += s.total_cost_tokens
        p["turns"] += s.turns
        p["sessions"] += 1
        if s.caveman:
            p["caveman_sessions"] += 1
    return dict(by_proj)


def caveman_comparison(sessions: list[SessionStats]) -> dict:
    groups = {"caveman": [], "normal": []}
    for s in sessions:
        if s.turns == 0:
            continue
        key = "caveman" if s.caveman else "normal"
        groups[key].append(s)

    def stats(sess_list):
        if not sess_list:
            return {"sessions": 0, "turns": 0, "avg_tokens_per_turn": 0, "avg_output_per_turn": 0}
        total_turns = sum(s.turns for s in sess_list)
        total_cost = sum(s.total_cost_tokens for s in sess_list)
        total_output = sum(s.output_tokens for s in sess_list)
        return {
            "sessions": len(sess_list),
            "turns": total_turns,
            "avg_tokens_per_turn": round(total_cost / total_turns, 1) if total_turns else 0,
            "avg_output_per_turn": round(total_output / total_turns, 1) if total_turns else 0,
        }

    c = stats(groups["caveman"])
    n = stats(groups["normal"])
    savings_pct = 0.0
    if n["avg_tokens_per_turn"] > 0 and c["avg_tokens_per_turn"] > 0:
        savings_pct = round((1 - c["avg_tokens_per_turn"] / n["avg_tokens_per_turn"]) * 100, 1)

    return {
        "caveman": c,
        "normal": n,
        "savings_pct": savings_pct,
    }


def rolling_windows(turns: list[dict]) -> dict:
    """Compute token usage across rolling time windows relative to now."""
    now_ms = int(time.time() * 1000)

    windows_ms = {
        "1h":  1 * 3600 * 1000,
        "5h":  5 * 3600 * 1000,
        "24h": 24 * 3600 * 1000,
    }

    result = {}
    for label, span_ms in windows_ms.items():
        cutoff = now_ms - span_ms
        window_turns = [t for t in turns if t.get("ts", 0) >= cutoff]
        cost = sum(t["cost"] for t in window_turns)
        out = sum(t["output"] for t in window_turns)
        n = len(window_turns)
        result[label] = {
            "turns": n,
            "cost_tokens": cost,
            "output_tokens": out,
            "tokens_per_hour": round(cost / (span_ms / 3_600_000), 1),
        }

    # Burn rate: tokens/hour over last 1h, projected against 5h window
    burn_1h = result["1h"]["tokens_per_hour"]
    cost_5h = result["5h"]["cost_tokens"]

    # Hours remaining until 5h window oldest turn drops off
    # Approximate: if burning at current 1h rate, how many hours until 5h window fills
    # We don't know the hard limit, so emit raw data for frontend to use with user threshold
    result["burn_rate_per_hour"] = burn_1h
    result["now_ts"] = now_ms

    # Historical peak: sliding 5h window over all turn data
    span_5h_ms = 5 * 3600 * 1000
    sorted_turns = sorted(turns, key=lambda t: t.get("ts", 0))
    peak_5h = 0
    if sorted_turns:
        left = 0
        window_cost = 0
        for right, t in enumerate(sorted_turns):
            window_cost += t["cost"]
            while sorted_turns[right]["ts"] - sorted_turns[left]["ts"] > span_5h_ms:
                window_cost -= sorted_turns[left]["cost"]
                left += 1
            if window_cost > peak_5h:
                peak_5h = window_cost
    result["historical_peak_5h"] = peak_5h
    # Suggested soft limit: peak + 10% headroom, rounded to nearest 100K
    if peak_5h > 0:
        suggested = int(round(peak_5h * 1.1 / 100_000) * 100_000)
        suggested = max(suggested, 100_000)
    else:
        suggested = 500_000
    result["suggested_limit"] = suggested

    # Hourly buckets for last 24h sparkline (24 buckets)
    buckets: dict[int, int] = defaultdict(int)
    cutoff_24h = now_ms - 24 * 3600 * 1000
    for t in turns:
        ts = t.get("ts", 0)
        if ts < cutoff_24h:
            continue
        hour_bucket = int((ts - cutoff_24h) // (3600 * 1000))  # 0-23
        buckets[hour_bucket] += t["cost"]
    result["hourly_buckets_24h"] = [buckets.get(i, 0) for i in range(24)]

    return result
