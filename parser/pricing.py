"""
Dollar cost per turn, two ways:

  api_usd — what the tokens would cost at Anthropic API list prices (pricing.toml). Useful for
            quoting work and comparing projects regardless of plan.
  sub_usd — your subscription fee for that calendar month, split across turns by their share of
            the month's api_usd. What the usage actually cost you. Needs `[billing]` in tags.toml:

      [billing]
      plans = [ { from = "2026-04-01", usd_per_month = 100 },
                { from = "2026-09-15", usd_per_month = 200 } ]   # plan in effect at month end
"""

import os
import pathlib
import tomllib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

PRICING_CONFIG = pathlib.Path(os.environ.get(
    "PRICING_CONFIG", str(pathlib.Path(__file__).with_name("pricing.toml"))))
M = 1_000_000


@dataclass
class PriceConfig:
    models: dict                 # substring → {input, output, cache_read}
    write_5m: float
    write_1h: float

    def rates(self, model: str) -> dict:
        for key, rates in self.models.items():
            if key != "default" and key in (model or ""):
                return rates
        return self.models["default"]


@dataclass
class Billing:
    plans: list = field(default_factory=list)   # [(from "YYYY-MM-DD", usd_per_month)], any order

    def fee_for_month(self, month: str) -> float | None:
        """Fee of the plan in effect at the end of `month` ("YYYY-MM")."""
        fee = None
        for start, usd in sorted(self.plans):
            if start[:7] <= month:
                fee = usd
        return fee


def load_config_text(text: str) -> PriceConfig:
    raw = tomllib.loads(text)
    return PriceConfig(models=raw["models"], write_5m=raw["cache_write_5m_multiplier"],
                       write_1h=raw["cache_write_1h_multiplier"])


def load_config(path: pathlib.Path = PRICING_CONFIG) -> PriceConfig:
    return load_config_text(path.read_text())


def load_billing(tags_toml: pathlib.Path) -> Billing:
    try:
        raw = tomllib.loads(tags_toml.read_text()).get("billing", {})
    except FileNotFoundError:
        return Billing()
    return Billing(plans=[(str(p["from"]), float(p["usd_per_month"])) for p in raw.get("plans", [])])


def turn_usd(t: dict, cfg: PriceConfig) -> float:
    r = cfg.rates(t.get("model", ""))
    w1h = t.get("cache_write_1h", 0)
    w5m = t["cache_creation"] - w1h   # unsplit (older/backfilled) writes price at the 5m rate
    return (t["input"] * r["input"] + t["output"] * r["output"] + t["cache_read"] * r["cache_read"]
            + w5m * r["input"] * cfg.write_5m + w1h * r["input"] * cfg.write_1h) / M


def _month(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=UTC).strftime("%Y-%m")


def annotate(sessions: list, cfg: PriceConfig, billing: Billing) -> None:
    """Set `api_usd` and `sub_usd` on every turn (in place)."""
    month_total = defaultdict(float)
    for s in sessions:
        for t in s.turns_raw:
            t["api_usd"] = turn_usd(t, cfg)
            month_total[_month(t["ts"])] += t["api_usd"]
    for s in sessions:
        for t in s.turns_raw:
            month = _month(t["ts"])
            fee = billing.fee_for_month(month)
            total = month_total[month]
            t["sub_usd"] = fee * t["api_usd"] / total if fee is not None and total else None
