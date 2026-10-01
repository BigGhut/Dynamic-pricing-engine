import random

from fastapi.testclient import TestClient

from run_simulation import SimulationRunner
from src import config
from src.eval.switchback import run
from src.pricing import accept_probability, quote_fare


def test_cherry_pick_lowers_short_multiplicative_accept():
    duration, distance, ds_ratio = 180.0, 2.0, 3.0
    additive = quote_fare(duration, distance, ds_ratio, "ADDITIVE")
    multiplicative = quote_fare(duration, distance, ds_ratio, "MULTIPLICATIVE")
    p_additive = accept_probability(
        price=additive.price,
        distance_km=distance,
        duration_sec=duration,
        arm="ADDITIVE",
        surge_multiplier=additive.surge_multiplier,
    )
    p_multiplicative = accept_probability(
        price=multiplicative.price,
        distance_km=distance,
        duration_sec=duration,
        arm="MULTIPLICATIVE",
        surge_multiplier=multiplicative.surge_multiplier,
    )
    assert p_multiplicative < p_additive


def test_morning_destinations_prefer_the_center():
    random.seed(0)
    simulator = SimulationRunner(num_drivers=1, verbose=False)
    morning = [simulator.get_dest_node_probabilistic(8.0) for _ in range(200)]
    evening = [simulator.get_dest_node_probabilistic(18.0) for _ in range(200)]
    center = set(simulator.center_nodes)
    suburb = set(simulator.suburb_nodes)
    assert sum(node in center for node in morning) > 140
    assert sum(node in suburb for node in evening) > 140


def test_search_returns_the_priced_trip(monkeypatch):
    from src.api.main import app
    from src.models.road_graph import RoadGraph

    monkeypatch.setattr(config, "CAUSAL_ENABLED", False)
    graph = RoadGraph()
    origin = graph.nodes["center"]
    dest = graph.nodes["mkad_0"]

    with TestClient(app) as client:
        client.post("/api/v1/virtual_hour", json={"hour": 11.0})
        response = client.post(
            "/api/v1/search",
            json={
                "search_id": "quote-1",
                "lat": origin["lat"],
                "lon": origin["lon"],
                "dest_lat": dest["lat"],
                "dest_lon": dest["lon"],
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["is_fail_static"] is False
    assert body["test_group"] == "ADDITIVE"
    assert body["price"] >= config.MIN_FARE
    assert body["distance_km"] > 5
    assert body["duration_sec"] > 0


def test_switchback_eval_is_deterministic():
    first = run(n_ticks=4, seed=1, num_drivers=20)
    second = run(n_ticks=4, seed=1, num_drivers=20)
    assert first == second
    assert first["n_trips"] > 0
    assert {"ADDITIVE", "MULTIPLICATIVE"} <= set(first["arms"])
    for stats in first["arms"].values():
        assert 0.0 <= stats["accept_rate"] <= 1.0


def test_demand_model_beats_mean_on_holdout():
    from src.models.demand_model import DemandElasticityModel

    model = DemandElasticityModel()
    frame = model.generate_synthetic_data(800)
    model.fit_dataframe(frame.iloc[:600], iterations=30)
    holdout = frame.iloc[600:]
    predictions = model.model.predict(holdout[["price", "demand_supply_ratio", "competitor_price", "hour"]])
    residual = (predictions - holdout["conversion"].to_numpy()) ** 2
    baseline = holdout["conversion"].to_numpy() - frame.iloc[:600]["conversion"].mean()
    assert residual.mean() < (baseline ** 2).mean()
