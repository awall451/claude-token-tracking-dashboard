"""
Energy, water and carbon estimates from token counts.

Coefficients live in energy.toml (override with ENERGY_CONFIG). Every estimate is a
[low, central, high] triple because providers publish no per-token energy data.
"""

import os
import pathlib
import tomllib
from dataclasses import dataclass

ENERGY_CONFIG = pathlib.Path(os.environ.get(
    "ENERGY_CONFIG", str(pathlib.Path(__file__).with_name("energy.toml"))))

# turn dict key → energy.toml [wh_per_mtok] key
_TOKEN_CLASSES = {
    "input": "input",
    "cache_creation": "cache_write",
    "cache_read": "cache_read",
    "output": "output",
}


@dataclass
class EnergyConfig:
    wh_per_mtok: dict          # class → [low, central, high]
    model_multiplier: dict     # substring → factor; "default" fallback
    grid_gco2_per_kwh: float
    water_onsite_ml_per_wh: float

    def multiplier(self, model: str) -> float:
        for key, factor in self.model_multiplier.items():
            if key != "default" and key in (model or ""):
                return factor
        return self.model_multiplier.get("default", 1.0)


def load_config_text(text: str) -> EnergyConfig:
    raw = tomllib.loads(text)
    return EnergyConfig(
        wh_per_mtok={k: [float(x) for x in v] for k, v in raw["wh_per_mtok"].items()},
        model_multiplier=dict(raw.get("model_multiplier", {})),
        grid_gco2_per_kwh=float(raw["grid_gco2_per_kwh"]),
        water_onsite_ml_per_wh=float(raw["water_onsite_ml_per_wh"]),
    )


def load_config(path: pathlib.Path = ENERGY_CONFIG) -> EnergyConfig:
    return load_config_text(path.read_text())


def turn_wh(turn: dict, config: EnergyConfig) -> list[float]:
    """[low, central, high] Wh for one assistant request."""
    factor = config.multiplier(turn.get("model", ""))
    out = [0.0, 0.0, 0.0]
    if not factor:
        return out
    for turn_key, cls in _TOKEN_CLASSES.items():
        tokens = turn.get(turn_key, 0)
        if not tokens:
            continue
        for i, rate in enumerate(config.wh_per_mtok[cls]):
            out[i] += tokens / 1_000_000 * rate * factor
    return out


def impact(wh: list[float], config: EnergyConfig) -> dict:
    """Convert a Wh triple into water (mL, on-site) and carbon (gCO2e) triples."""
    return {
        "wh": [round(x, 3) for x in wh],
        "water_ml": [round(x * config.water_onsite_ml_per_wh, 3) for x in wh],
        "gco2": [round(x / 1000 * config.grid_gco2_per_kwh, 3) for x in wh],
    }


def annotate_sessions(sessions: list, config: EnergyConfig) -> None:
    """Set `wh` on every turn (in place) so aggregates can sum it."""
    for s in sessions:
        for t in s.turns_raw:
            t["wh"] = turn_wh(t, config)


def config_summary(config: EnergyConfig) -> dict:
    """Coefficients echoed into stats.json so the frontend can show its assumptions."""
    return {
        "wh_per_mtok": config.wh_per_mtok,
        "model_multiplier": config.model_multiplier,
        "grid_gco2_per_kwh": config.grid_gco2_per_kwh,
        "water_onsite_ml_per_wh": config.water_onsite_ml_per_wh,
    }
