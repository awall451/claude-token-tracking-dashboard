import pytest

from parser import energy
from parser.models import SessionStats, aggregate_by_turn_project

CFG = energy.load_config_text("""
grid_gco2_per_kwh = 400
water_onsite_ml_per_wh = 1.0

[wh_per_mtok]          # [low, central, high]
input       = [100, 200, 400]
cache_write = [100, 200, 400]
cache_read  = [10, 20, 40]
output      = [1000, 2000, 4000]

[model_multiplier]
haiku = 0.2
"<synthetic>" = 0.0
default = 1.0
""")


def _turn(**kw):
    t = {"ts": 1, "model": "claude-opus-5-5", "input": 0, "cache_creation": 0, "cache_read": 0, "output": 0, "cost": 0}
    t.update(kw)
    return t


def test_turn_energy_sums_token_classes():
    t = _turn(input=1_000_000, cache_read=1_000_000, output=500_000)
    assert energy.turn_wh(t, CFG) == pytest.approx([100 + 10 + 500, 200 + 20 + 1000, 400 + 40 + 2000])


def test_model_multiplier_matches_family_substring():
    t = _turn(model="claude-haiku-4-5-20251001", output=1_000_000)
    assert energy.turn_wh(t, CFG) == pytest.approx([200, 400, 800])


def test_synthetic_turns_cost_nothing():
    assert energy.turn_wh(_turn(model="<synthetic>", output=1_000_000), CFG) == [0, 0, 0]


def test_impact_converts_water_and_carbon():
    imp = energy.impact([1000.0, 2000.0, 4000.0], CFG)
    assert imp["wh"] == [1000.0, 2000.0, 4000.0]
    assert imp["water_ml"] == [1000.0, 2000.0, 4000.0]
    assert imp["gco2"] == [400.0, 800.0, 1600.0]


def test_annotate_sessions_sets_turn_wh_and_aggregates_sum_it():
    s = SessionStats(session_id="s", project_path="/p")
    s.turns_raw = [_turn(output=1_000_000, project="a"), _turn(output=500_000, project="a")]
    energy.annotate_sessions([s], CFG)
    assert s.turns_raw[0]["wh"] == pytest.approx([1000, 2000, 4000])
    assert s.to_dict()["wh"] == pytest.approx([1500, 3000, 6000])
    assert aggregate_by_turn_project([s])["a"]["wh"] == pytest.approx([1500, 3000, 6000])


def test_default_config_ships_and_is_ordered():
    cfg = energy.load_config()
    for lo, mid, hi in cfg.wh_per_mtok.values():
        assert 0 < lo <= mid <= hi
    assert cfg.grid_gco2_per_kwh > 0
