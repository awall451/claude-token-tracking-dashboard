from datetime import datetime, timezone

from parser.models import (
    SessionStats,
    aggregate_by_day,
    aggregate_by_project,
    caveman_comparison,
    lifetime_stats,
    rolling_windows,
)


def _session(**kwargs) -> SessionStats:
    defaults = dict(session_id="s", project_path="/p", turns=1)
    defaults.update(kwargs)
    return SessionStats(**defaults)


def _ts(year, month, day) -> int:
    return int(datetime(year, month, day, tzinfo=timezone.utc).timestamp() * 1000)


def test_session_stats_total_cost_excludes_cache_reads():
    s = _session(input_tokens=100, cache_creation_tokens=200, cache_read_tokens=999, output_tokens=50)
    assert s.total_cost_tokens == 350
    assert s.total_tokens == 1349


def test_session_stats_cache_hit_rate():
    s = _session(cache_creation_tokens=100, cache_read_tokens=300)
    assert s.cache_hit_rate == 0.75
    empty = _session()
    assert empty.cache_hit_rate == 0.0


def test_session_stats_avg_tokens_per_turn_zero_turns():
    s = _session(turns=0, input_tokens=500)
    assert s.avg_tokens_per_turn == 0.0


def test_aggregate_by_project_sums_same_project_and_counts_caveman():
    sessions = [
        _session(project_path="/a", input_tokens=100, output_tokens=10, turns=2),
        _session(project_path="/a", input_tokens=200, output_tokens=20, turns=3, caveman=True),
        _session(project_path="/b", input_tokens=50, output_tokens=5, turns=1),
    ]
    out = aggregate_by_project(sessions)
    assert out["/a"]["input_tokens"] == 300
    assert out["/a"]["output_tokens"] == 30
    assert out["/a"]["turns"] == 5
    assert out["/a"]["sessions"] == 2
    assert out["/a"]["caveman_sessions"] == 1
    assert out["/b"]["sessions"] == 1
    assert out["/b"]["caveman_sessions"] == 0


def test_aggregate_by_project_falls_back_to_unknown():
    sessions = [_session(project_path="", input_tokens=10, turns=1)]
    out = aggregate_by_project(sessions)
    assert "unknown" in out
    assert out["unknown"]["input_tokens"] == 10


def test_aggregate_by_day_groups_by_utc_date_and_skips_zero_ts():
    sessions = [
        _session(start_ts=_ts(2026, 4, 1), input_tokens=100, turns=1),
        _session(start_ts=_ts(2026, 4, 1), input_tokens=200, turns=2, caveman=True),
        _session(start_ts=_ts(2026, 4, 2), input_tokens=50, turns=1),
        _session(start_ts=0, input_tokens=999, turns=1),  # skipped
    ]
    out = aggregate_by_day(sessions)
    assert list(out.keys()) == ["2026-04-01", "2026-04-02"]
    assert out["2026-04-01"]["input_tokens"] == 300
    assert out["2026-04-01"]["caveman_sessions"] == 1
    assert out["2026-04-01"]["normal_sessions"] == 1
    assert out["2026-04-02"]["sessions"] == 1


def test_caveman_comparison_savings_pct_and_wenyan_normalization():
    sessions = [
        _session(turns=10, input_tokens=1000),  # normal: 100/turn
        _session(turns=10, input_tokens=600, caveman=True, caveman_mode="full"),  # caveman: 60/turn
        _session(turns=5, input_tokens=200, caveman=True, caveman_mode="wenyan-ultra"),
    ]
    out = caveman_comparison(sessions)
    assert out["normal"]["avg_tokens_per_turn"] == 100.0
    # caveman bucket aggregates both caveman sessions: (600+200) / (10+5) = 53.3
    assert out["caveman"]["avg_tokens_per_turn"] == 53.3
    # savings vs normal 100 -> 1 - 0.533 = 46.7%
    assert out["savings_pct"] == 46.7
    # wenyan-* normalized into single "wenyan" bucket
    assert "wenyan" in out["by_mode"]
    assert "wenyan-ultra" not in out["by_mode"]
    assert out["by_mode"]["wenyan"]["sessions"] == 1


def test_caveman_comparison_skips_zero_turn_sessions():
    sessions = [_session(turns=0, input_tokens=999, caveman=True)]
    out = caveman_comparison(sessions)
    assert out["caveman"]["sessions"] == 0
    assert out["savings_pct"] == 0.0


def test_lifetime_stats_peak_day_and_rolling_7d():
    sessions = [
        _session(start_ts=_ts(2026, 4, 1), input_tokens=100, turns=1),
        _session(start_ts=_ts(2026, 4, 2), input_tokens=500, turns=1),  # peak
        _session(start_ts=_ts(2026, 4, 3), input_tokens=200, turns=1),
    ]
    by_day = aggregate_by_day(sessions)
    out = lifetime_stats(sessions, by_day)
    assert out["total_cost_tokens"] == 800
    assert out["peak_day"]["date"] == "2026-04-02"
    assert out["peak_day"]["cost_tokens"] == 500
    assert out["days_tracked"] == 3
    # rolling 7d trailing average has one entry per day
    assert len(out["rolling_7d_average"]) == 3
    # third day's trailing avg = (100 + 500 + 200) / 3
    assert out["rolling_7d_average"][2]["avg_cost_tokens"] == 266.7


def test_rolling_windows_suggested_limit_rounding_and_floor():
    # No turns -> default suggested_limit floor of 500_000
    out = rolling_windows([])
    assert out["suggested_limit"] == 500_000
    assert out["historical_peak_5h"] == 0
    assert out["1h"]["turns"] == 0
    # Sparse data -> peak just from one turn, suggested rounded to nearest 100K with 10% headroom + floor
    out2 = rolling_windows([{"ts": 0, "cost": 50_000, "output": 1}])
    assert out2["historical_peak_5h"] == 50_000
    # 50_000 * 1.1 = 55_000 -> rounds to 100_000 -> floor 100_000
    assert out2["suggested_limit"] == 100_000
