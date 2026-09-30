import pytest

from parser import pricing
from parser.models import SessionStats

CFG = pricing.load_config()

M = 1_000_000


def _turn(model, **kw):
    t = {"ts": 1790676000000, "model": model, "input": 0, "cache_creation": 0, "cache_write_5m": 0,
         "cache_write_1h": 0, "cache_read": 0, "output": 0, "cost": 0}
    t.update(kw)
    return t


def test_fable_rates_with_1h_cache_writes():
    # Matches a real cost-state: fable-5-1 17,642 in / 226,870 out / 82.39M read / 1.958M 1h writes ≈ $71.29
    t = _turn("claude-fable-5-1", input=17642, output=226870, cache_read=82_393_878,
              cache_creation=1_958_746, cache_write_1h=1_958_746)
    assert pricing.turn_usd(t, CFG) == pytest.approx(71.29, abs=0.1)


def test_5m_writes_and_unsplit_writes():
    t5 = _turn("claude-opus-5-5", cache_creation=M, cache_write_5m=M)
    assert pricing.turn_usd(t5, CFG) == pytest.approx(4 * 1.25)
    # Old/backfilled records lack the 5m/1h split → priced at the 5m rate
    tu = _turn("claude-opus-5-5", cache_creation=M)
    assert pricing.turn_usd(tu, CFG) == pytest.approx(4 * 1.25)


def test_model_family_matching():
    assert pricing.turn_usd(_turn("claude-haiku-4-5-20251001", output=M), CFG) == pytest.approx(5)
    assert pricing.turn_usd(_turn("claude-sonnet-4-6", input=M), CFG) == pytest.approx(3)
    assert pricing.turn_usd(_turn("claude-opus-5", input=M), CFG) == pytest.approx(5)
    assert pricing.turn_usd(_turn("claude-opus-5-5", cache_read=M), CFG) == pytest.approx(0.20)
    assert pricing.turn_usd(_turn("<synthetic>", output=M), CFG) == 0


def _session(turns):
    s = SessionStats(session_id="s", project_path="/p")
    s.turns_raw = turns
    return s


def test_subscription_share_splits_monthly_fee_by_api_usd():
    sep, oct_ = 1790676000000, 1790676000000 + 5 * 24 * 3600 * 1000
    a = _turn("claude-opus-5-5", ts=sep, output=M)          # $20
    b = _turn("claude-opus-5-5", ts=sep, output=3 * M)      # $60
    c = _turn("claude-opus-5-5", ts=oct_, output=M)         # only October usage
    billing = pricing.Billing(plans=[("2026-01-01", 100.0)])
    pricing.annotate([_session([a, b, c])], CFG, billing)
    assert a["sub_usd"] == pytest.approx(25) and b["sub_usd"] == pytest.approx(75)
    assert c["sub_usd"] == pytest.approx(100)


def test_plan_changes_by_effective_date():
    billing = pricing.Billing(plans=[("2026-01-01", 20.0), ("2026-09-15", 200.0)])
    assert billing.fee_for_month("2026-08") == 20.0
    assert billing.fee_for_month("2026-09") == 200.0   # plan in effect at month end
    assert pricing.Billing(plans=[]).fee_for_month("2026-09") is None


def test_no_billing_config_leaves_sub_usd_none():
    t = _turn("claude-opus-5-5", output=M)
    pricing.annotate([_session([t])], CFG, pricing.Billing(plans=[]))
    assert t["api_usd"] == pytest.approx(20) and t["sub_usd"] is None
