import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from src import config
from src.api.pricing_service import price_search
from src.api.schemas import CompetitorPriceRequest, FaultInjectionRequest, PingRequest, PriceResponse, SearchRequest
from src.data.database import get_db_connection, init_db
from src.data.feature_store import FeatureStore
from src.data.outbox import OutboxWorker
from src.features.driver_history import DriverHistoryStore

# Глобальные инстансы сервисов
feature_store = None
outbox_worker = None
driver_history_store = DriverHistoryStore()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Контекстный менеджер жизненного цикла FastAPI."""
    global feature_store, outbox_worker, driver_history_store

    print("[API] Запуск инициализации сервисов...")
    init_db()
    feature_store = FeatureStore()
    driver_history_store = DriverHistoryStore(db_path=config.DB_PATH)
    outbox_worker = OutboxWorker()
    outbox_worker.start()

    yield

    print("[API] Остановка фоновых воркеров...")
    if outbox_worker:
        outbox_worker.stop()
        outbox_worker.join(timeout=2.0)
    print("[API] Завершение работы сервиса.")

app = FastAPI(
    title="Dynamic Pricing Engine (DPE) MVP API",
    description="Цена поездки по дорожному графу: кратчайший путь и switchback surge. CatBoost в котировку не входит.",
    version="1.0.0",
    lifespan=lifespan
)

# Разрешаем CORS для Streamlit и веб-клиентов
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health", status_code=status.HTTP_200_OK)
def health_check():
    """Проверка доступности сервиса."""
    return {"status": "healthy", "timestamp": time.time()}

@app.post("/api/v1/ping", status_code=status.HTTP_200_OK)
def ping_driver(payload: PingRequest):
    """
    Регистрация местоположения водителя.
    Вызывается мобильным приложением водителя с заданной периодичностью (например, раз в 5-10 сек).
    """
    try:
        if payload.edge_u and payload.edge_v and payload.progress is not None:
            feature_store.register_driver_ping_graph(
                payload.driver_id, payload.edge_u, payload.edge_v, payload.progress, payload.lat, payload.lon
            )
        else:
            feature_store.register_driver_ping(payload.driver_id, payload.lat, payload.lon)
        return {"status": "success", "message": f"Driver {payload.driver_id} registered"}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error registering driver ping: {e}"
        ) from e

@app.post("/api/v1/competitor", status_code=status.HTTP_200_OK)
def update_competitor_price(payload: CompetitorPriceRequest):
    """
    Регистрация цен конкурентов.
    Вызывается внешними парсерами или скриптами мониторинга конкурентов.
    """
    try:
        feature_store.set_competitor_price(payload.h3_index, payload.price)
        return {"status": "success", "message": f"Competitor price updated for {payload.h3_index}"}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error updating competitor price: {e}"
        ) from e

@app.post("/api/v1/search", response_model=PriceResponse, status_code=status.HTTP_200_OK)
def request_price(payload: SearchRequest):
    """Цена поездки: граф, switchback surge, бизнес-ограничения, опциональный CPE."""
    return price_search(feature_store, driver_history_store, payload)


@app.get("/api/v1/explain/{h3_index}", status_code=status.HTTP_200_OK)
def get_price_history_and_explanation(h3_index: str, limit: int = 10):
    """
    Возвращает историю ценообразования и объяснения причин
    изменений для аудита алгоритма ценообразования.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT price, base_price, surge_multiplier, explanation, created_at
            FROM prices
            WHERE h3_index = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (h3_index, limit)
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]

@app.post("/api/v1/inject_fault", status_code=status.HTTP_200_OK)
def inject_fault(payload: FaultInjectionRequest):
    """Включает или выключает симуляцию сбоя в ML-микросервисе."""
    config.FAULT_INJECTION_ACTIVE = payload.enabled
    status_str = "activated" if payload.enabled else "deactivated"
    print(f"[API] [FAULT_INJECTION] Fault injection {status_str}")
    return {"status": "success", "fault_injection_active": config.FAULT_INJECTION_ACTIVE}

@app.post("/api/v1/telemetry/edge", status_code=status.HTTP_200_OK)
def register_edge_telemetry(payload: dict):
    """Регистрирует телеметрию скорости на конкретном ребре графа."""
    u = payload.get("u")
    v = payload.get("v")
    speed = payload.get("speed")
    if u and v and speed is not None:
        feature_store.register_edge_telemetry(u, v, speed)
        return {"status": "success"}
    return {"status": "error", "message": "Invalid telemetry payload"}

@app.get("/api/v1/graph/state", status_code=status.HTTP_200_OK)
def get_graph_state():
    """Возвращает структуру графа и текущие динамические скорости ребер."""
    try:
        edge_speeds = feature_store.get_dynamic_edge_weights()
        serialized_speeds = {f"{u}->{v}": speed for (u, v), speed in edge_speeds.items()}
        
        # Для каждого ребра также отдадим базовую скорость для сравнения
        edge_metadata = {}
        for u, neighbors in feature_store.graph.adj.items():
            for v, edge_data in neighbors.items():
                edge_metadata[f"{u}->{v}"] = {
                    "base_speed": edge_data["base_speed_kmh"],
                    "road_type": edge_data["road_type"],
                    "distance_km": edge_data["distance_km"]
                }
        # Получим последние цены для H3 ячеек из SQLite
        latest_prices = {}
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT p.h3_index, p.price, p.surge_multiplier, p.surge_bonus, p.test_group
                    FROM prices p
                    INNER JOIN (
                        SELECT h3_index, MAX(created_at) as max_ts
                        FROM prices
                        GROUP BY h3_index
                    ) latest ON p.h3_index = latest.h3_index AND p.created_at = latest.max_ts
                    """
                )
                for row in cursor.fetchall():
                    latest_prices[row["h3_index"]] = {
                        "price": row["price"],
                        "surge_multiplier": row["surge_multiplier"],
                        "surge_bonus": row["surge_bonus"],
                        "test_group": row["test_group"]
                    }
        except Exception as db_err:
            print(f"[API] [WARN] Failed to load latest prices: {db_err}")
                
        return {
            "nodes": feature_store.graph.nodes,
            "edges": {u: list(neighbors.keys()) for u, neighbors in feature_store.graph.adj.items()},
            "edge_speeds": serialized_speeds,
            "edge_metadata": edge_metadata,
            "latest_prices": latest_prices
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting graph state: {e}"
        ) from e

@app.post("/api/v1/virtual_hour", status_code=status.HTTP_200_OK)
def set_virtual_hour(payload: dict):
    """Устанавливает текущее время симуляции для расчета заторов."""
    hour = payload.get("hour", 12.0)
    feature_store.set_sim_virtual_hour(hour)
    return {"status": "success", "virtual_hour": hour}

