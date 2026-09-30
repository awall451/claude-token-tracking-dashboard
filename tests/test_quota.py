import json

from parser import quota

H5 = 5 * 3600


def _snap(ts, five=None, seven=None):
    rl = {}
    if five:
        rl["five_hour"] = {"used_percentage": five[0], "resets_at": five[1]}
    if seven:
        rl["seven_day"] = {"used_percentage": seven[0], "resets_at": seven[1]}
    return {"ts": ts, "session_id": "s", "rate_limits": rl}


def _write(path, snaps):
    path.write_text("".join(json.dumps(s) + "\n" for s in snaps) + "not json\n")


def test_missing_log_returns_none(tmp_path):
    assert quota.summarize(tmp_path / "nope.jsonl", turns=[], now=0) is None


def test_latest_snapshot_per_window(tmp_path):
    log = tmp_path / "q.jsonl"
    _write(log, [
        _snap(100, five=(10, 1000), seven=(30, 9000)),
        _snap(200, five=(20, 1000)),            # seven_day absent in this one
    ])
    out = quota.summarize(log, turns=[], now=250)
    assert out["five_hour"]["used_percentage"] == 20
    assert out["five_hour"]["captured_at"] == 200
    assert out["seven_day"]["used_percentage"] == 30
    assert out["age_seconds"] == 50


def test_implied_five_hour_limit_from_tokens_in_current_window(tmp_path):
    log = tmp_path / "q.jsonl"
    resets = 20_000
    start = resets - H5
    _write(log, [_snap(start + 3600, five=(25, resets))])
    turns = [
        {"ts": (start - 10) * 1000, "cost": 999_999},    # before window: ignored
        {"ts": (start + 60) * 1000, "cost": 100_000},
        {"ts": (start + 1800) * 1000, "cost": 150_000},
        {"ts": (start + 7200) * 1000, "cost": 777},      # after snapshot: ignored
    ]
    out = quota.summarize(log, turns=turns, now=start + 3700)
    assert out["five_hour"]["window_cost_tokens"] == 250_000
    assert out["five_hour"]["implied_limit_cost_tokens"] == 1_000_000


def test_no_implied_limit_below_min_percentage(tmp_path):
    log = tmp_path / "q.jsonl"
    _write(log, [_snap(100, five=(2, 1000 + H5))])
    out = quota.summarize(log, turns=[{"ts": 150_000, "cost": 10}], now=200)
    assert out["five_hour"]["implied_limit_cost_tokens"] is None


def test_history_is_downsampled_series(tmp_path):
    log = tmp_path / "q.jsonl"
    _write(log, [_snap(t, five=(t / 100, 10**9), seven=(t / 1000, 10**9)) for t in range(0, 7200, 60)])
    out = quota.summarize(log, turns=[], now=7200)
    assert out["history"][0] == {"ts": 0, "five_hour": 0.0, "seven_day": 0.0}
    assert all(b["ts"] - a["ts"] >= 900 for a, b in zip(out["history"], out["history"][1:]))
