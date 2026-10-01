"""HTTP and in-process clients for the traffic simulator."""

import os

import requests

from src import clock
from src.api.pricing_service import price_search
from src.api.schemas import SearchRequest


class HttpEngine:
    """Talks to a running DPE API. A connection error on ping means the server is down."""

    def __init__(self, api_url: str | None = None):
        self.api_url = (api_url or os.getenv("API_URL", "http://localhost:8000")).rstrip("/")

    def _post(self, path: str, payload: dict, timeout: float) -> None:
        try:
            requests.post(f"{self.api_url}{path}", json=payload, timeout=timeout)
        except Exception:
            pass

    def set_hour(self, hour: float) -> None:
        self._post("/api/v1/virtual_hour", {"hour": hour}, timeout=0.5)

    def telemetry(self, u: str, v: str, speed: float) -> None:
        self._post("/api/v1/telemetry/edge", {"u": u, "v": v, "speed": speed}, timeout=0.3)

    def ping(self, driver: dict) -> str:
        try:
            requests.post(
                f"{self.api_url}/api/v1/ping",
                json={
                    "driver_id": driver["id"],
                    "lat": driver["lat"],
                    "lon": driver["lon"],
                    "edge_u": driver["edge_u"],
                    "edge_v": driver["edge_v"],
                    "progress": driver["progress"],
                },
                timeout=0.8,
            )
            return "ok"
        except requests.exceptions.ConnectionError:
            return "down"
        except Exception:
            return "ok"

    def competitor(self, node_id: str, price: float) -> None:
        self._post("/api/v1/competitor", {"h3_index": node_id, "price": price}, timeout=0.5)

    def search(self, body: dict) -> dict | None:
        try:
            response = requests.post(f"{self.api_url}/api/v1/search", json=body, timeout=0.8)
            if response.status_code == 200:
                return response.json()
        except Exception as error:
            print(f"[Simulator] [ERROR] Ошибка симуляции запроса: {error}")
        return None


class DirectEngine:
    """Prices trips in-process. Used by the seeded eval, not by the live HTTP loop."""

    def __init__(self, feature_store, driver_history):
        self.feature_store = feature_store
        self.driver_history = driver_history

    def set_hour(self, hour: float) -> None:
        self.feature_store.set_sim_virtual_hour(hour)

    def telemetry(self, u: str, v: str, speed: float) -> None:
        self.feature_store.register_edge_telemetry(u, v, speed)

    def ping(self, driver: dict) -> str:
        self.feature_store.register_driver_ping_graph(
            driver["id"],
            driver["edge_u"],
            driver["edge_v"],
            driver["progress"],
            driver["lat"],
            driver["lon"],
        )
        return "ok"

    def competitor(self, node_id: str, price: float) -> None:
        self.feature_store.set_competitor_price(node_id, price)

    def search(self, body: dict) -> dict | None:
        clock.advance(0.05)
        response = price_search(self.feature_store, self.driver_history, SearchRequest(**body))
        if hasattr(response, "model_dump"):
            return response.model_dump()
        return response.dict()
