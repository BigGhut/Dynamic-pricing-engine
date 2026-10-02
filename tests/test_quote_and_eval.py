import random

import requests
from fastapi.testclient import TestClient

from src import config
from src.eval.switchback import run
from src.pricing import quote_fare
from src.runtime import HttpEngine
from src.sim.driver_model import accept_probability
from src.sim.runner import SimulationRunner


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
    assert multiplicative.surge_multiplier > 1.1
    assert p_multiplicative < p_additive


def test_cherry_penalty_is_optional_and_does_not_touch_additive():
    duration, distance, ds_ratio = 180.0, 2.0, 3.0
    additive = quote_fare(duration, distance, ds_ratio, "ADDITIVE")
    multiplicative = quote_fare(duration, distance, ds_ratio, "MULTIPLICATIVE")
    p_additive_on = accept_probability(
        price=additive.price,
        distance_km=distance,
        duration_sec=duration,
        arm="ADDITIVE",
        surge_multiplier=additive.surge_multiplier,
        apply_cherry_penalty=True,
    )
    p_additive_off = accept_probability(
        price=additive.price,
        distance_km=distance,
        duration_sec=duration,
        arm="ADDITIVE",
        surge_multiplier=additive.surge_multiplier,
        apply_cherry_penalty=False,
    )
    p_multiplicative_on = accept_probability(
        price=multiplicative.price,
        distance_km=distance,
        duration_sec=duration,
        arm="MULTIPLICATIVE",
        surge_multiplier=multiplicative.surge_multiplier,
        apply_cherry_penalty=True,
    )
    p_multiplicative_off = accept_probability(
        price=multiplicative.price,
        distance_km=distance,
        duration_sec=duration,
        arm="MULTIPLICATIVE",
        surge_multiplier=multiplicative.surge_multiplier,
        apply_cherry_penalty=False,
    )
    assert p_additive_on == p_additive_off
    assert p_multiplicative_off > p_multiplicative_on


def test_sign_flip_of_three_positive_diffs():
    from src.eval.switchback import sign_flip_test

    result = sign_flip_test([1.0, 1.0, 1.0])
    assert result["n_pairs"] == 3
    assert result["n_assignments"] == 8
    assert result["n_as_extreme"] == 2
    assert result["two_sided_p"] == 0.25
    assert result["observed_mean_diff"] == 1.0


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
    monkeypatch.setattr(config, "SIM_MODE", True)
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
    assert set(first["policies"]) == {"with_cherry_penalty", "no_cherry_penalty"}
    assert set(first["reading"]) == {"short_trips", "sign_flip", "cv"}
    for policy in first["policies"].values():
        assert {"ADDITIVE", "MULTIPLICATIVE"} <= set(policy["arms"])
        assert len(policy["hours"]) == 24
        assert len(policy["pairs"]) == 12
        assert "sign_flip_accept_short" in policy
        assert "sign_flip_accept_rate" in policy
        for stats in policy["arms"].values():
            assert stats["accept_rate"] is None or 0.0 <= stats["accept_rate"] <= 1.0
    with_penalty = first["policies"]["with_cherry_penalty"]["arms"]
    no_penalty = first["policies"]["no_cherry_penalty"]["arms"]
    assert with_penalty["ADDITIVE"] == no_penalty["ADDITIVE"]
    assert with_penalty["MULTIPLICATIVE"]["mean_price"] == no_penalty["MULTIPLICATIVE"]["mean_price"]
    assert with_penalty["MULTIPLICATIVE"]["accept_long"] == no_penalty["MULTIPLICATIVE"]["accept_long"]


def test_sim_routes_stay_hidden_until_sim_mode(monkeypatch):
    from src.api.main import app

    monkeypatch.setattr(config, "SIM_MODE", False)
    monkeypatch.setattr(config, "FAULT_INJECTION_ACTIVE", False)
    with TestClient(app) as client:
        assert "/api/v1/virtual_hour" not in client.get("/openapi.json").json()["paths"]
        assert client.post("/api/v1/virtual_hour", json={"hour": 11.0}).status_code == 404
        assert client.post("/api/v1/inject_fault", json={"enabled": True}).status_code == 404
        assert client.post(
            "/api/v1/telemetry/edge",
            json={"u": "center", "v": "mkad_0", "speed": 30},
        ).status_code == 404
    monkeypatch.setattr(config, "SIM_MODE", True)
    with TestClient(app) as client:
        assert client.post("/api/v1/virtual_hour", json={"hour": 11.0}).status_code == 200
        turned_on = client.post("/api/v1/inject_fault", json={"enabled": True})
        assert turned_on.status_code == 200
        assert turned_on.json()["fault_injection_active"] is True
        client.post("/api/v1/inject_fault", json={"enabled": False})


def test_http_engine_counts_rejected_posts(monkeypatch):
    engine = HttpEngine("http://127.0.0.1:9")

    def boom(*args, **kwargs):
        raise requests.ConnectionError("down")

    monkeypatch.setattr("src.runtime.requests.post", boom)
    engine.telemetry("center", "mkad_0", 30.0)
    engine.set_hour(8.0)
    assert engine.post_errors == 2


def test_demand_model_beats_mean_on_holdout():
    from experiments.demand_model import DemandElasticityModel

    model = DemandElasticityModel()
    frame = model.generate_synthetic_data(800)
    model.fit_dataframe(frame.iloc[:600], iterations=30)
    holdout = frame.iloc[600:]
    predictions = model.model.predict(holdout[["price", "demand_supply_ratio", "competitor_price", "hour"]])
    residual = (predictions - holdout["conversion"].to_numpy()) ** 2
    baseline = holdout["conversion"].to_numpy() - frame.iloc[:600]["conversion"].mean()
    assert residual.mean() < (baseline ** 2).mean()
