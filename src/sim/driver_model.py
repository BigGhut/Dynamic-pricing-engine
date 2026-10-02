"""Planted driver. This is not on the quote path.

Acceptance is a logit of profit per hour. The 900 ₽/hour threshold and the
cherry-pick penalty are simulator assumptions, not a measured driver response.
"""

import math

COMMISSION = 0.20
COST_PER_KM = 6.0
PICKUP_SEC = 240.0
LOGIT_K = 0.004
UTILITY_THRESHOLD = 900.0
SHORT_TRIP_KM = 5.0
CHERRY_SURGE = 1.1


def accept_probability(
    *,
    price: float,
    distance_km: float,
    duration_sec: float,
    arm: str,
    surge_multiplier: float,
    apply_cherry_penalty: bool = True,
) -> float:
    """Probability that the planted driver accepts the trip.

    Pass apply_cherry_penalty=False to score the same trip without the penalty.
    """
    revenue = price * (1.0 - COMMISSION)
    cost = distance_km * COST_PER_KM
    hours = (duration_sec + PICKUP_SEC) / 3600.0
    utility = (revenue - cost) / max(hours, 0.05)
    if (
        apply_cherry_penalty
        and arm == "MULTIPLICATIVE"
        and distance_km < SHORT_TRIP_KM
        and surge_multiplier > CHERRY_SURGE
    ):
        penalty = max(0.15, 1.0 - 0.85 * (surge_multiplier - 1.0))
        utility *= penalty
    return 1.0 / (1.0 + math.exp(-LOGIT_K * (utility - UTILITY_THRESHOLD)))


def trip_earnings(price: float, distance_km: float, accepted: int) -> float:
    """Net driver earnings for one offered trip. A rejection earns nothing."""
    if not accepted:
        return 0.0
    return price * (1.0 - COMMISSION) - distance_km * COST_PER_KM
