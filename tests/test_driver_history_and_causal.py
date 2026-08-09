"""Tests for driver history tracking and Causal Engine integration flags in DPE."""

import pytest
from pathlib import Path
from src.features.driver_history import DriverHistoryStore
from src import config


def test_driver_history_store_get_and_record():
    store = DriverHistoryStore()
    
    # Initially 0.0, 0.0 for unknown driver
    p_trips, a_surge = store.get_driver_features("driver_101")
    assert p_trips == 0.0
    assert a_surge == 0.0

    # Record first trip with surge_bonus 2.0
    store.record_trip(driver_id="driver_101", surge_bonus=2.0, h3_cell="cell_a")
    p_trips, a_surge = store.get_driver_features("driver_101")
    assert p_trips == 1.0
    assert a_surge == 2.0

    # Record second trip with surge_bonus 4.0
    store.record_trip(driver_id="driver_101", surge_bonus=4.0, h3_cell="cell_a")
    p_trips, a_surge = store.get_driver_features("driver_101")
    assert p_trips == 2.0
    assert a_surge == 3.0  # (2.0 + 4.0) / 2


def test_driver_history_cell_fallback():
    store = DriverHistoryStore()

    # Before first record -> 0.0, 0.0
    p_trips0, a_surge0 = store.get_driver_features(driver_id=None, h3_cell="cell_xyz")
    assert p_trips0 == 0.0
    assert a_surge0 == 0.0

    # First record with surge 5.0
    store.record_trip(driver_id=None, surge_bonus=5.0, h3_cell="cell_xyz")
    p_trips1, a_surge1 = store.get_driver_features(driver_id=None, h3_cell="cell_xyz")
    assert p_trips1 == 1.0
    assert a_surge1 == 5.0

    # Second record with surge 3.0
    store.record_trip(driver_id=None, surge_bonus=3.0, h3_cell="cell_xyz")
    p_trips2, a_surge2 = store.get_driver_features(driver_id=None, h3_cell="cell_xyz")
    assert p_trips2 == 2.0
    assert a_surge2 == 4.0  # (5.0 + 3.0) / 2


def test_history_matches_cpe_connector_parity():
    """Verify DriverHistoryStore step-by-step replay produces exact prior cumulative stats."""
    store = DriverHistoryStore()
    driver_id = "drv_test"
    surges = [1.0, 3.0, 5.0, 0.0]

    # Expected prior stats before each trip:
    # trip 0: past_trips=0.0, avg_surge=0.0
    # trip 1: past_trips=1.0, avg_surge=1.0
    # trip 2: past_trips=2.0, avg_surge=2.0 ( (1+3)/2 )
    # trip 3: past_trips=3.0, avg_surge=3.0 ( (1+3+5)/3 )
    expected = [
        (0.0, 0.0),
        (1.0, 1.0),
        (2.0, 2.0),
        (3.0, 3.0),
    ]

    for i, surge in enumerate(surges):
        p_trips, a_surge = store.get_driver_features(driver_id)
        exp_trips, exp_surge = expected[i]
        assert p_trips == exp_trips
        assert abs(a_surge - exp_surge) < 1e-6
        store.record_trip(driver_id, surge_bonus=surge)


def test_causal_enabled_config_flag(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CAUSAL_ENABLED", "false")
    # Re-evaluate logic or config
    causal_enabled = getattr(config, "CAUSAL_ENABLED", True)
    assert isinstance(causal_enabled, bool)


def test_causal_fields_in_search_response(monkeypatch: pytest.MonkeyPatch):
    """Test search response schema includes causal_* fields when calling search endpoint."""
    from fastapi.testclient import TestClient
    from src.api.main import app

    monkeypatch.setattr(config, "CAUSAL_ENABLED", False)

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/search",
            json={
                "search_id": "test_search_causal_1",
                "driver_id": "drv_test_c1",
                "lat": 55.7558,
                "lon": 37.6173,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "causal_uplift_score" in data
        assert "causal_override" in data
        assert "causal_recommended_treatment" in data
        assert data["causal_override"] is False
        assert data["causal_uplift_score"] is None



