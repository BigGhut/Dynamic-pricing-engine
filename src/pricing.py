"""Trip fare and the planted driver-acceptance model.

The quote is a closed formula. CatBoost is not on this path.
Acceptance is a logit of driver profit per hour, with an extra penalty
on short multiplicative trips. That penalty is an assumption of the
simulator, not a measured driver response.
"""

import math
from dataclasses import dataclass

from src import config

COMMISSION = 0.20
COST_PER_KM = 6.0
PICKUP_SEC = 240.0
LOGIT_K = 0.004
UTILITY_THRESHOLD = 900.0
SHORT_TRIP_KM = 5.0
CHERRY_SURGE = 1.1


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


def accept_probability(
    *,
    price: float,
    distance_km: float,
    duration_sec: float,
    arm: str,
    surge_multiplier: float,
) -> float:
    """Probability that the planted driver model accepts the trip."""
    revenue = price * (1.0 - COMMISSION)
    cost = distance_km * COST_PER_KM
    hours = (duration_sec + PICKUP_SEC) / 3600.0
    utility = (revenue - cost) / max(hours, 0.05)
    if arm == "MULTIPLICATIVE" and distance_km < SHORT_TRIP_KM and surge_multiplier > CHERRY_SURGE:
        penalty = max(0.15, 1.0 - 0.85 * (surge_multiplier - 1.0))
        utility *= penalty
    return 1.0 / (1.0 + math.exp(-LOGIT_K * (utility - UTILITY_THRESHOLD)))


def trip_earnings(price: float, distance_km: float, accepted: int) -> float:
    """Net driver earnings for one offered trip. A rejection earns nothing."""
    if not accepted:
        return 0.0
    return price * (1.0 - COMMISSION) - distance_km * COST_PER_KM
