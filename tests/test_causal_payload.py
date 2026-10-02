"""CPE is asked about one treatment: the additive surcharge on a named driver."""

import pytest
from fastapi.testclient import TestClient

from src import config
from src.service.pricing import build_causal_payload


def test_payload_is_the_driver_and_pre_treatment_features():
    body = build_causal_payload("drv_1", 5.0, 600.0, 11.0, 3.0, 1.5)
    assert body == {
        "driver_id": "drv_1",
        "features": {
            "distance_km": 5.0,
            "duration_sec": 600.0,
            "hour_of_day": 11.0,
            "past_trips": 3.0,
            "avg_surge": 1.5,
        },
    }
    assert "user_id" not in body
    assert "price" not in body["features"]
    assert "surge_bonus" not in body["features"]


def test_only_an_additive_hour_with_a_driver_calls_cpe(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "SIM_MODE", True)
    monkeypatch.setattr(config, "CAUSAL_ENABLED", True)
    calls: list[dict] = []
    state = {"supports": None}

    class _Response:
        status_code = 200

        def json(self):
            body = {"uplift_score": -0.2, "recommended_treatment": "NO_SURCHARGE"}
            if state["supports"] is not None:
                body["ranking_supports_decision"] = state["supports"]
            if state["supports"] is True:
                body["score_threshold"] = 0.05
            return body

    def fake_post(url, json, timeout):
        calls.append({"url": url, "json": json, "timeout": timeout})
        return _Response()

    monkeypatch.setattr("src.service.pricing.httpx.post", fake_post)

    from src.api.main import app

    search = {
        "search_id": "causal_contract",
        "driver_id": "drv_causal",
        "lat": 55.7558,
        "lon": 37.6173,
    }
    with TestClient(app) as client:
        assert client.post("/api/v1/virtual_hour", json={"hour": 12.0}).status_code == 200
        even = client.post("/api/v1/search", json=search)
        assert even.status_code == 200
        assert calls == []
        assert even.json()["causal_override"] is False

        anonymous = dict(search)
        anonymous.pop("driver_id")
        assert client.post("/api/v1/virtual_hour", json={"hour": 11.0}).status_code == 200
        no_driver = client.post("/api/v1/search", json=anonymous)
        assert no_driver.status_code == 200
        assert calls == []

        state["supports"] = False
        noisy = client.post("/api/v1/search", json=search)
        assert noisy.status_code == 200
        assert noisy.json()["causal_override"] is False
        assert noisy.json()["test_group"] != "CAUSAL_NO_SURGE"

        state["supports"] = True
        odd = client.post("/api/v1/search", json=search)
        assert odd.status_code == 200
        assert len(calls) == 2
        body = calls[0]["json"]
        assert body["driver_id"] == "drv_causal"
        assert set(body["features"]) == {
            "distance_km",
            "duration_sec",
            "hour_of_day",
            "past_trips",
            "avg_surge",
        }
        assert body["features"]["hour_of_day"] == pytest.approx(11.0)
        quoted = odd.json()
        assert quoted["causal_override"] is True
        assert quoted["test_group"] == "CAUSAL_NO_SURGE"
        assert quoted["causal_recommended_treatment"] == "NO_SURCHARGE"
