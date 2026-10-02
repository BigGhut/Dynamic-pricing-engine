"""Seeded switchback of additive versus multiplicative surge.

The driver model is planted. The graph is the 70-node sketch, not OSM.
`python -m src.eval.switchback` rewrites reports/metrics.json with both
cherry-penalty policies. The penalty does not change the quoted price.
"""

import argparse
import json
import random
import shutil
import tempfile
from pathlib import Path

import pandas as pd

from src import clock, config
from src.data.database import init_db
from src.data.feature_store import FeatureStore
from src.features.driver_history import DriverHistoryStore
from src.runtime import DirectEngine
from src.sim.driver_model import SHORT_TRIP_KM, trip_earnings
from src.sim.runner import SimulationRunner

ROOT = Path(__file__).resolve().parents[2]

METRICS_PATH = ROOT / "reports" / "metrics.json"
CLOCK_ORIGIN = 1_700_000_000.0
TICK_CLOCK_SECONDS = 4.0
POLICY_COLUMNS = {
    "with_cherry_penalty": "accepted_with_cherry_penalty",
    "no_cherry_penalty": "accepted_no_cherry_penalty",
}
# 2^16 is still cheap, and a switchback day has at most 12 pairs.
MAX_SIGN_FLIP_PAIRS = 16


def _expected_arm(hour: int) -> str:
    return "ADDITIVE" if hour % 2 == 1 else "MULTIPLICATIVE"


def _slice_rates(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {
            "n": 0,
            "n_short": 0,
            "n_long": 0,
            "accept_rate": None,
            "accept_short": None,
            "accept_long": None,
        }
    short = frame[frame["distance_km"] < SHORT_TRIP_KM]
    long = frame[frame["distance_km"] >= 10.0]
    return {
        "n": int(len(frame)),
        "n_short": int(len(short)),
        "n_long": int(len(long)),
        "accept_rate": float(frame["accepted"].mean()),
        "accept_short": None if short.empty else float(short["accepted"].mean()),
        "accept_long": None if long.empty else float(long["accepted"].mean()),
    }


def _arm_stats(frame: pd.DataFrame) -> dict:
    stats = _slice_rates(frame)
    if frame.empty:
        stats["mean_price"] = None
        stats["mean_driver_income"] = None
        stats["cv_driver_income"] = None
        return stats
    earnings = frame.groupby("driver_id")["driver_earnings"].sum()
    mean_income = float(earnings.mean()) if len(earnings) else None
    cv = None
    if len(earnings) >= 2 and mean_income not in (None, 0.0):
        cv = float(earnings.std(ddof=1) / mean_income)
    stats["mean_price"] = float(frame["price"].mean())
    stats["mean_driver_income"] = mean_income
    stats["cv_driver_income"] = cv
    return stats


def sign_flip_test(diffs: list[float]) -> dict:
    """Exact two-sided sign-flip test for a mean of paired differences.

    Each pair keeps its magnitude and takes both signs. With 12 pairs that
    is 4096 assignments. Resampling so few pairs hides the tails, so this
    enumerates them. The result is a null reference, not a confidence interval.
    """
    n = len(diffs)
    estimand = "mean of (additive hour - previous multiplicative hour)"
    if n == 0:
        return {
            "estimand": estimand,
            "n_pairs": 0,
            "n_assignments": 0,
            "observed_mean_diff": None,
            "n_as_extreme": None,
            "two_sided_p": None,
            "diffs": [],
        }
    if n > MAX_SIGN_FLIP_PAIRS:
        raise ValueError(f"sign-flip enumeration expects at most {MAX_SIGN_FLIP_PAIRS} pairs, got {n}")
    observed = sum(diffs) / n
    extreme = 0
    total = 1 << n
    for mask in range(total):
        signed = 0.0
        for i, diff in enumerate(diffs):
            signed += -diff if mask & (1 << i) else diff
        # The identity mask must count. The tolerance covers binary float noise.
        if abs(signed / n) + 1e-12 >= abs(observed):
            extreme += 1
    return {
        "estimand": estimand,
        "n_pairs": n,
        "n_assignments": total,
        "observed_mean_diff": observed,
        "n_as_extreme": extreme,
        "two_sided_p": extreme / total,
        "diffs": list(diffs),
    }


def _pairs(hours: list[dict]) -> list[dict]:
    by_hour = {row["hour"]: row for row in hours}
    pairs = []
    for even in range(0, 24, 2):
        left = by_hour[even]
        right = by_hour[even + 1]
        accept_diff = None
        if left["n"] > 0 and right["n"] > 0:
            accept_diff = right["accept_rate"] - left["accept_rate"]
        short_diff = None
        if left["n_short"] > 0 and right["n_short"] > 0:
            short_diff = right["accept_short"] - left["accept_short"]
        pairs.append({
            "even_hour": even,
            "odd_hour": even + 1,
            "multiplicative_n": left["n"],
            "additive_n": right["n"],
            "accept_rate_diff": accept_diff,
            "multiplicative_n_short": left["n_short"],
            "additive_n_short": right["n_short"],
            "accept_short_diff": short_diff,
        })
    return pairs


def _policy_report(frame: pd.DataFrame, column: str) -> dict:
    scored = frame.copy()
    scored["accepted"] = scored[column].astype(int)
    scored["driver_earnings"] = [
        trip_earnings(price, distance, int(accepted))
        for price, distance, accepted in zip(
            scored["price"], scored["distance_km"], scored["accepted"], strict=True
        )
    ]
    mismatch = 0
    for hour, arm in zip(scored["hour"], scored["test_group"], strict=True):
        if arm != _expected_arm(int(hour)):
            mismatch += 1
    arms = {
        arm: _arm_stats(scored[scored["test_group"] == arm])
        for arm in ("ADDITIVE", "MULTIPLICATIVE")
    }
    hours = []
    for hour in range(24):
        arm = _expected_arm(hour)
        subset = scored[(scored["hour"] == hour) & (scored["test_group"] == arm)]
        row = _slice_rates(subset)
        row["hour"] = hour
        row["arm"] = arm
        hours.append(row)
    pairs = _pairs(hours)
    short_diffs = [pair["accept_short_diff"] for pair in pairs if pair["accept_short_diff"] is not None]
    rate_diffs = [pair["accept_rate_diff"] for pair in pairs if pair["accept_rate_diff"] is not None]
    return {
        "arms": arms,
        "hours": hours,
        "pairs": pairs,
        "n_arm_mismatch": mismatch,
        "sign_flip_accept_short": sign_flip_test(short_diffs),
        "sign_flip_accept_rate": sign_flip_test(rate_diffs),
    }


def _pct(rate: float) -> str:
    return f"{100.0 * rate:.1f}%"


def _pp(gap: float) -> str:
    return f"{100.0 * gap:.1f} п.п."


def _short_reading(with_arms: dict, no_arms: dict) -> str:
    additive_with = with_arms["ADDITIVE"]["accept_short"]
    multiplicative_with = with_arms["MULTIPLICATIVE"]["accept_short"]
    additive_without = no_arms["ADDITIVE"]["accept_short"]
    multiplicative_without = no_arms["MULTIPLICATIVE"]["accept_short"]
    if None in (additive_with, multiplicative_with, additive_without, multiplicative_without):
        return "На этом прогоне не у каждой руки есть короткие поездки, разрыв не интерпретируется."
    gap_with = additive_with - multiplicative_with
    gap_without = additive_without - multiplicative_without
    text = (
        f"Со штрафом короткие поездки короче 5 км принимаются в {_pct(multiplicative_with)} случаев "
        f"в мультипликативной руке и в {_pct(additive_with)} в аддитивной (разрыв {_pp(gap_with)}). "
        f"Без штрафа — {_pct(multiplicative_without)} и {_pct(additive_without)} (разрыв {_pp(gap_without)})."
    )
    if abs(gap_without) <= 0.05:
        text += (
            " Без штрафа разрыв пропадает. Результат со штрафом целиком заложен этим допущением: "
            "это честный вывод, а не слабость модели."
        )
    elif abs(gap_with) > 1e-12 and (abs(gap_with) - abs(gap_without)) / abs(gap_with) >= 0.5:
        text += (
            " Большая часть разрыва — штраф. Остаток остаётся, потому что сами цены рук различаются: "
            "штраф меняет только вероятность принятия, не котировку."
        )
    else:
        text += (
            " Штраф этот разрыв не объясняет целиком: без него короткие поездки всё ещё принимаются по-разному, "
            "потому что котировки рук различаются."
        )
    return text


def _flip_clause(label: str, result: dict) -> str:
    if result["n_pairs"] == 0 or result["two_sided_p"] is None:
        return f"{label}: пар, где в обоих часах есть этот срез, нет."
    mean = result["observed_mean_diff"]
    return (
        f"{label}: {result['n_pairs']} пар, средняя разница {mean:+.3f}, "
        f"{result['n_as_extreme']} из {result['n_assignments']} назначений не менее экстремальны, "
        f"p = {result['two_sided_p']:.4f}."
    )


def _dropped_short_pairs(policy: dict) -> str:
    dropped = [pair for pair in policy["pairs"] if pair["accept_short_diff"] is None]
    if not dropped:
        return "Короткие поездки есть в обеих частях всех 12 пар, поэтому перебор даёт 4096 назначений."
    listed = ", ".join(
        f"{pair['even_hour']}–{pair['odd_hour']} "
        f"(коротких {pair['multiplicative_n_short']} и {pair['additive_n_short']})"
        for pair in dropped
    )
    result = policy["sign_flip_accept_short"]
    used = [pair for pair in policy["pairs"] if pair["accept_short_diff"] is not None]
    small = sum(
        1
        for pair in used
        if pair["multiplicative_n_short"] < 5 or pair["additive_n_short"] < 5
    )
    return (
        f"В коротких поездках нет разницы у пар {listed}: долю там не из чего считать. "
        f"В тест входят {result['n_pairs']} пар и {result['n_assignments']} назначений, не 4096. "
        f"В {small} из {len(used)} таких пар хотя бы в одном часе меньше 5 коротких поездок, "
        "и у каждой пары один вес: тест говорит о среднем этих часовых долей, а не об отдельных поездках. "
        "По всем поездкам пары полные, это 12 пар и 4096 назначений."
    )


def _sign_reading(with_policy: dict, no_policy: dict) -> str:
    return " ".join([
        "Одни виртуальные сутки: 96 тиков по 15 минут, 12 часов на каждую руку. "
        "Час 7 собран из тика в начале суток и тика в конце, остальные часы — по четыре тика.",
        "Пара — чётный час (мультипликативная рука) и следующий нечётный (аддитивная). "
        "Разница — доля принятия в аддитивном часе минус доля в мультипликативном.",
        "Бутстрэп по такому числу пар даёт слишком узкий интервал: при ресемплинге почти не видно хвостов. "
        "Поэтому здесь точный перестановочный тест. У каждой пары оба знака разницы, "
        "и наблюдаемая средняя сравнивается со всеми назначениями. Это не доверительный интервал.",
        _dropped_short_pairs(with_policy),
        _flip_clause("Короткие, со штрафом", with_policy["sign_flip_accept_short"]),
        _flip_clause("Короткие, без штрафа", no_policy["sign_flip_accept_short"]),
        _flip_clause("Все поездки, со штрафом", with_policy["sign_flip_accept_rate"]),
        _flip_clause("Все поездки, без штрафа", no_policy["sign_flip_accept_rate"]),
    ])


def _cv_reading(with_arms: dict, no_arms: dict) -> str:
    cv_additive = with_arms["ADDITIVE"]["cv_driver_income"]
    cv_multiplicative = with_arms["MULTIPLICATIVE"]["cv_driver_income"]
    income_additive = with_arms["ADDITIVE"]["mean_driver_income"]
    income_multiplicative = with_arms["MULTIPLICATIVE"]["mean_driver_income"]
    if None in (cv_additive, cv_multiplicative, income_additive, income_multiplicative):
        return "CV дохода на этом прогоне не считается: слишком мало водителей или нулевой средний доход."
    text = (
        f"Со штрафом CV аддитивной руки {cv_additive:.3f}, мультипликативной {cv_multiplicative:.3f}, "
        f"средний доход аддитивной руки {income_additive:.1f} ₽, мультипликативной {income_multiplicative:.1f} ₽. "
        "Один прогон, seed 42, второго seed нет. "
        "CV делит разброс на среднее, поэтому разница CV может получиться из-за того, "
        "что аддитивная рука чаще принимает короткие поездки."
    )
    cv_additive_off = no_arms["ADDITIVE"]["cv_driver_income"]
    cv_multiplicative_off = no_arms["MULTIPLICATIVE"]["cv_driver_income"]
    income_additive_off = no_arms["ADDITIVE"]["mean_driver_income"]
    income_multiplicative_off = no_arms["MULTIPLICATIVE"]["mean_driver_income"]
    if None not in (cv_additive_off, cv_multiplicative_off, income_additive_off, income_multiplicative_off):
        text += (
            f" Без штрафа аддитивная рука остаётся на CV {cv_additive_off:.3f} и {income_additive_off:.1f} ₽, "
            f"мультипликативная — {income_multiplicative_off:.1f} ₽ и CV {cv_multiplicative_off:.3f}. "
            "Короткие поездки при этом принимаются почти одинаково. "
            "Пока seed один, из CV и среднего дохода отдельный вывод не следует."
        )
    return text


def _reading(policies: dict) -> dict:
    with_policy = policies["with_cherry_penalty"]
    no_policy = policies["no_cherry_penalty"]
    return {
        "short_trips": _short_reading(with_policy["arms"], no_policy["arms"]),
        "sign_flip": _sign_reading(with_policy, no_policy),
        "cv": _cv_reading(with_policy["arms"], no_policy["arms"]),
    }


def summarize(rows: list[dict], *, seed: int, n_ticks: int, num_drivers: int) -> dict:
    frame = pd.DataFrame(rows)
    if frame.empty:
        frame = pd.DataFrame(columns=[
            "test_group", "hour", "distance_km", "price", "driver_id",
            "accepted_with_cherry_penalty", "accepted_no_cherry_penalty",
        ])
    policies = {
        name: _policy_report(frame, column)
        for name, column in POLICY_COLUMNS.items()
    }
    return {
        "seed": seed,
        "n_ticks": n_ticks,
        "num_drivers": num_drivers,
        "n_trips": int(len(rows)),
        "tick_virtual_hours": 0.25,
        "tick_clock_seconds": TICK_CLOCK_SECONDS,
        "started_virtual_hour": 7.66,
        "graph_nodes": 70,
        "causal_enabled": False,
        "short_trip_km": SHORT_TRIP_KM,
        "acceptance_model": (
            "Planted logit of driver profit per hour. "
            "Short multiplicative trips with surge above 1.1x get an extra penalty "
            "unless the no-penalty column is used. Both columns share one uniform draw. "
            "This is not a city A/B."
        ),
        "od_pattern": "Morning suburb to center, evening center to suburb, otherwise uniform.",
        "policies": policies,
        "reading": _reading(policies),
    }


def run(n_ticks: int = 96, seed: int = 42, num_drivers: int = 80) -> dict:
    """Run the in-process simulator and return both switchback policies."""
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


def _print_policy(name: str, policy: dict) -> None:
    print(name)
    for arm, stats in policy["arms"].items():
        accept = stats["accept_rate"]
        short = stats["accept_short"]
        cv = stats["cv_driver_income"]
        print(
            f"  {arm}: n={stats['n']} accept={None if accept is None else round(accept, 3)} "
            f"short={None if short is None else round(short, 3)} "
            f"cv={None if cv is None else round(cv, 3)}"
        )
    for key in ("sign_flip_accept_short", "sign_flip_accept_rate"):
        result = policy[key]
        print(
            f"  {key}: pairs={result['n_pairs']} assignments={result['n_assignments']} "
            f"p={result['two_sided_p']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Seeded switchback, both cherry-penalty policies")
    parser.add_argument(
        "--no-cherry-penalty",
        action="store_true",
        help="Принято для совместимости с симулятором. Отчёт всегда пишет обе колонки.",
    )
    args = parser.parse_args()
    if args.no_cherry_penalty:
        print(
            "Отчёт содержит обе политики. Флаг меняет принятые поездки только в "
            "`python scripts/run_simulation.py --no-cherry-penalty`."
        )
    payload = run()
    METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    METRICS_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(METRICS_PATH)
    for name, policy in payload["policies"].items():
        _print_policy(name, policy)
    for paragraph in payload["reading"].values():
        print(paragraph)


if __name__ == "__main__":
    main()
