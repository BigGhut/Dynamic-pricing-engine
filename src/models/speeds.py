from src import config


def congestion_factor(road_type: str, hour: int) -> float:
    """Return the historical speed factor for a road type at this hour."""
    profiles = config.CONGESTION_PROFILES.get(road_type)
    if not profiles:
        return 1.0
    for (start, end), factor in profiles.items():
        if start <= end:
            if start <= hour < end:
                return factor
        elif hour >= start or hour < end:
            return factor
    return 1.0
