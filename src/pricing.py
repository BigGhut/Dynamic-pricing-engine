"""Trip fare. A closed formula. CatBoost and the driver model are not on this path."""

import math
from dataclasses import dataclass

from src import config


@dataclass(frozen=True)
class Quote:
    arm: str
    price: float
    base_fare: float
    surge_bonus: float
    surge_multiplier: float
    payout_formula: str


def base_fare(duration_sec: float, distance_km: float) -> float:
    raw = duration_sec * config.RATE_PER_SECOND + distance_km * config.RATE_PER_KILOMETER
    return max(raw, config.MIN_FARE)


def quote_fare(duration_sec: float, distance_km: float, ds_ratio: float, arm: str) -> Quote:
    """Price one trip before the business-rule caps."""
    fare = base_fare(duration_sec, distance_km)
    excess = max(0.0, ds_ratio - 1.0)
    if arm == "ADDITIVE":
        rate = config.ALPHA_SURGE_RATE * excess
        fixed = config.BETA_SURGE_VAL * excess
        decay = 1.0 - math.exp(-config.LAMBDA_DECAY * duration_sec)
        bonus = rate * duration_sec + fixed * decay
        price = fare + bonus
        formula = f"{round(fare, 1)} + {round(bonus, 1)}"
    elif arm == "MULTIPLICATIVE":
        bonus = 0.0
        multiplier = 1.0 + excess * 0.5
        price = fare * multiplier
        formula = f"{round(fare, 1)} * {round(multiplier, 2)}x"
    else:
        raise ValueError(f"unknown pricing arm: {arm}")
    multiplier = price / fare if fare else 1.0
    return Quote(arm, price, fare, bonus, multiplier, formula)
