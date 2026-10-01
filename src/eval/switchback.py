"""Seeded switchback of additive versus multiplicative surge.

The driver model is planted. The graph is the 70-node sketch, not OSM.
`python -m src.eval.switchback` rewrites reports/metrics.json.
"""

import json
import random
import shutil
import tempfile
from pathlib import Path

import pandas as pd

from run_simulation import SimulationRunner
from src import clock, config
from src.data.database import init_db
from src.data.feature_store import FeatureStore
from src.features.driver_history import DriverHistoryStore
from src.pricing import SHORT_TRIP_KM, trip_earnings
from src.runtime import DirectEngine

ROOT = Path(__file__).resolve().parents[2]

METRICS_PATH = ROOT / "reports" / "metrics.json"
CLOCK_ORIGIN = 1_700_000_000.0
TICK_CLOCK_SECONDS = 4.0


def _arm_stats(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {
            "n": 0,
            "n_short": 0,
            "n_long": 0,
            "accept_rate": None,
            "accept_short": None,
            "accept_long": None,
            "mean_price": None,
            "mean_driver_income": None,
            "cv_driver_income": None,
        }
    short = frame[frame["distance_km"] < SHORT_TRIP_KM]
    long = frame[frame["distance_km"] >= 10.0]
    earnings = frame.groupby("driver_id")["driver_earnings"].sum()
    mean_income = float(earnings.mean()) if len(earnings) else None
    cv = None
    if len(earnings) >= 2 and mean_income not in (None, 0.0):
        cv = float(earnings.std(ddof=1) / mean_income)
    return {
        "n": int(len(frame)),
        "n_short": int(len(short)),
        "n_long": int(len(long)),
        "accept_rate": float(frame["accepted"].mean()),
        "accept_short": None if short.empty else float(short["accepted"].mean()),
        "accept_long": None if long.empty else float(long["accepted"].mean()),
        "mean_price": float(frame["price"].mean()),
        "mean_driver_income": mean_income,
        "cv_driver_income": cv,
    }


def summarize(rows: list[dict], *, seed: int, n_ticks: int, num_drivers: int) -> dict:
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame["driver_earnings"] = [
            trip_earnings(price, distance, accepted)
            for price, distance, accepted in zip(
                frame["price"], frame["distance_km"], frame["accepted"], strict=True
            )
        ]
    arms = {}
    if not frame.empty:
        for arm in sorted(frame["test_group"].unique()):
            arms[str(arm)] = _arm_stats(frame[frame["test_group"] == arm])
    return {
        "seed": seed,
        "n_ticks": n_ticks,
        "num_drivers": num_drivers,
        "n_trips": int(len(frame)),
        "tick_virtual_hours": 0.25,
        "tick_clock_seconds": TICK_CLOCK_SECONDS,
        "started_virtual_hour": 7.66,
        "graph_nodes": 70,
        "causal_enabled": False,
        "short_trip_km": SHORT_TRIP_KM,
        "acceptance_model": (
            "Planted logit of driver profit per hour. "
            "Short multiplicative trips with surge above 1.1x get an extra penalty. "
            "This is not a city A/B."
        ),
        "od_pattern": "Morning suburb to center, evening center to suburb, otherwise uniform.",
        "arms": arms,
    }


def run(n_ticks: int = 96, seed: int = 42, num_drivers: int = 80) -> dict:
    """Run the in-process simulator and return the switchback summary."""
    saved_db = config.DB_PATH
    saved_causal = config.CAUSAL_ENABLED
    saved_fault = config.FAULT_INJECTION_ACTIVE
    saved_redis = config.USE_LOCAL_SIMULATED_REDIS
    saved_random = random.getstate()
    tmp = Path(tempfile.mkdtemp(prefix="dpe-eval-"))
    clock.set_frozen(CLOCK_ORIGIN)
    try:
        random.seed(seed)
        config.DB_PATH = str(tmp / "eval.db")
        config.CAUSAL_ENABLED = False
        config.FAULT_INJECTION_ACTIVE = False
        config.USE_LOCAL_SIMULATED_REDIS = True
        init_db()
        feature_store = FeatureStore()
        history = DriverHistoryStore(db_path=config.DB_PATH)
        engine = DirectEngine(feature_store, history)
        simulator = SimulationRunner(num_drivers=num_drivers, engine=engine, verbose=False)
        for _ in range(n_ticks):
            clock.advance(TICK_CLOCK_SECONDS)
            simulator.run_tick()
        return summarize(
            simulator.records,
            seed=seed,
            n_ticks=n_ticks,
            num_drivers=num_drivers,
        )
    finally:
        clock.set_frozen(None)
        random.setstate(saved_random)
        config.DB_PATH = saved_db
        config.CAUSAL_ENABLED = saved_causal
        config.FAULT_INJECTION_ACTIVE = saved_fault
        config.USE_LOCAL_SIMULATED_REDIS = saved_redis
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> None:
    payload = run()
    METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    METRICS_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(METRICS_PATH)
    for arm, stats in payload["arms"].items():
        accept = stats["accept_rate"]
        short = stats["accept_short"]
        cv = stats["cv_driver_income"]
        print(
            f"{arm}: n={stats['n']} accept={None if accept is None else round(accept, 3)} "
            f"short={None if short is None else round(short, 3)} "
            f"cv={None if cv is None else round(cv, 3)}"
        )


if __name__ == "__main__":
    main()
