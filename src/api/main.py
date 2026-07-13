import time
import math
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from src import config
from src.api.schemas import PingRequest, SearchRequest, PriceResponse, CompetitorPriceRequest, FaultInjectionRequest
from src.data.database import init_db, save_price_with_outbox, get_latest_price, get_db_connection
from src.data.feature_store import FeatureStore
from src.data.outbox import OutboxWorker
from src.models.demand_model import DemandElasticityModel
from src.models.geogrid import get_h3_index, smooth_metric_k_ring
from src.bre.rules import BusinessRulesEngine

# Глобальные инстансы сервисов
feature_store = None
demand_model = None
outbox_worker = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Контекстный менеджер жизненного цикла FastAPI."""
    global feature_store, demand_model, outbox_worker
    
    print("[API] Запуск инициализации сервисов...")
    
    # 1. Инициализируем БД
    init_db()
    
    # 2. Инициализируем Feature Store
    feature_store = FeatureStore()
    
    # 3. Инициализируем и обучаем/загружаем модель спроса
    demand_model = DemandElasticityModel()
    demand_model.load_or_train()
    
    # 4. Запускаем фоновый CDC воркер
    outbox_worker = OutboxWorker()
    outbox_worker.start()
    
    yield
    
    # Завершение работы
    print("[API] Остановка фоновых воркеров...")
    if outbox_worker:
        outbox_worker.stop()
        outbox_worker.join(timeout=2.0)
    print("[API] Завершение работы сервиса.")

app = FastAPI(
    title="Dynamic Pricing Engine (DPE) MVP API",
    description="Четырехуровневая система ценообразования на базе H3 и CatBoost",
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
        )

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
        )

@app.post("/api/v1/search", response_model=PriceResponse, status_code=status.HTTP_200_OK)
def request_price(payload: SearchRequest):
    """
    Основной эндпоинт расчета цены (Surge pricing).
    Поддерживает Switchback-тестирование (MULTIPLICATIVE vs ADDITIVE) и каскадный Fail-Static:
    1. Уровень 1: Графовые изохроны (Reverse Dijkstra) + Dijkstra OD-маршрут
    2. Уровень 2: Упрощенное графовое окружение (k-Ring смежных узлов без Дейкстры)
    3. Уровень 3: Плоский H3-расчет (MVP 2.0)
    4. Уровень 4: Фолбек на базовый тариф (150 руб.)
    """
    node_id = None
    h3_cell = None
    fallback_level = 0
    explanation = ""
    
    # Инициализация переменных для Switchback
    # Синхронизируем Switchback с виртуальным временем симулятора через Redis для ускорения теста
    vh_raw = feature_store.redis_client.get("sim:virtual_hour")
    virtual_hour = float(vh_raw) if vh_raw is not None else (time.time() / 3600.0) % 24
    current_hour = int(virtual_hour)
    is_additive = (current_hour // config.SWITCHBACK_WINDOW_HOURS) % 2 == 1
    test_group = "ADDITIVE" if is_additive else "MULTIPLICATIVE"
    
    trip_duration_sec = 900.0  # 15 минут по умолчанию
    trip_dist_km = 7.0         # 7 км по умолчанию
    base_fare = config.BASE_PRICE
    
    proposed_price = config.BASE_PRICE
    surge_bonus = 0.0
    payout_formula = ""
    
    try:
        # Проверяем инжекцию сбоя в ML-микросервисе
        if config.FAULT_INJECTION_ACTIVE:
            raise RuntimeError("CRITICAL ERROR: Simulated ML-microservice failure / crash!")

        # Шаг 0: Привязка координат и регистрация запроса
        node_id = feature_store.graph.snap_to_node(payload.lat, payload.lon)
        h3_cell = feature_store.graph.nodes[node_id]["h3_cell"]
        feature_store.register_search_request(payload.search_id, payload.lat, payload.lon)
        
        # Рассчитываем точные дистанцию и время по дорожному графу, если передан destination
        edge_speeds = feature_store.get_dynamic_edge_weights()
        if payload.dest_lat is not None and payload.dest_lon is not None:
            dest_node_id = feature_store.graph.snap_to_node(payload.dest_lat, payload.dest_lon)
            d_time, d_dist = feature_store.graph.shortest_path_od(node_id, dest_node_id, edge_speeds)
            if d_time != float("inf") and d_time > 0.0:
                trip_duration_sec = d_time
                trip_dist_km = d_dist

        # Расчет базового тарифа на основе дистанции и времени
        base_fare = max(
            trip_duration_sec * config.RATE_PER_SECOND + trip_dist_km * config.RATE_PER_KILOMETER,
            config.MIN_FARE
        )
        
        # Получаем текущий час
        hour = time.localtime(time.time()).tm_hour

        # Вспомогательная функция для расчета цены по ds_ratio
        def compute_surge_price(ds: float) -> tuple[float, float, str]:
            if test_group == "ADDITIVE":
                m_i = config.ALPHA_SURGE_RATE * max(0.0, ds - 1.0)
                z_i = config.BETA_SURGE_VAL * max(0.0, ds - 1.0)
                q = 1.0 - math.exp(-config.LAMBDA_DECAY * trip_duration_sec)
                bonus = m_i * trip_duration_sec + z_i * q
                p = base_fare + bonus
                formula = f"{round(base_fare, 1)} + {round(bonus, 1)}"
                return p, bonus, formula
            else:
                mult = 1.0 + max(0.0, ds - 1.0) * 0.5
                p = base_fare * mult
                formula = f"{round(base_fare, 1)} * {round(mult, 2)}x"
                return p, 0.0, formula

        # ==========================================
        # УРОВЕНЬ 1: Графовые изохроны (Reverse Dijkstra)
        # ==========================================
        try:
            # Получаем фичи для исходного узла
            features = feature_store.get_features_graph(node_id, edge_speeds)
            ds_ratio_raw = features["demand_supply_ratio"]
            
            # Применяем k-Ring сглаживание по дорожному графу (смежные узлы)
            ds_ratios = [ds_ratio_raw]
            weights = [1.0]
            for neighbor in feature_store.graph.adj[node_id]:
                neighbor_features = feature_store.get_features_graph(neighbor, edge_speeds)
                ds_ratios.append(neighbor_features["demand_supply_ratio"])
                weights.append(0.2)
            smoothed_ds_ratio = sum(r * w for r, w in zip(ds_ratios, weights)) / sum(weights)
            
            proposed_price, surge_bonus, payout_formula = compute_surge_price(smoothed_ds_ratio)
            
            prev_price_record = get_latest_price(h3_cell)
            previous_price = prev_price_record["price"] if prev_price_record else None
            
            final_price, bre_explanation = BusinessRulesEngine.apply_rules(
                h3_cell=h3_cell,
                proposed_price=proposed_price,
                previous_price=previous_price
            )
            explanation = f"Graph-based pricing. {bre_explanation}"
            fallback_level = 0
            
        # ==========================================
        # УРОВЕНЬ 2: k-Ring смежных узлов (без Дейкстры)
        # ==========================================
        except Exception as e1:
            print(f"[API] [WARN] Графовый расчет Уровня 1 не удался ({e1}). Откат на Уровень 2.")
            try:
                # Считаем водителей в самом узле и его непосредственных соседях (1 ребро)
                adj_nodes = list(feature_store.graph.adj[node_id].keys()) + [node_id]
                now = time.time()
                ts_bucket = int(now // 60) * 60
                
                active_drivers = set()
                buckets_for_drivers = [ts_bucket, ts_bucket - 60]
                for v in adj_nodes:
                    for bucket in buckets_for_drivers:
                        key = f"node:{v}:ts:{bucket}:drivers"
                        members = feature_store.redis_client.zrangebyscore(key, now - config.DRIVER_TTL_SEC, now)
                        for member in members:
                            parts = member.split(":")
                            if len(parts) == 4:
                                active_drivers.add(parts[0]) # driver_id
                
                drivers_in_isochrone = len(active_drivers)
                
                # Спрос
                searches_count = 0
                for i in range(5):
                    bucket = ts_bucket - (i * 60)
                    key = f"node:{node_id}:ts:{bucket}:searches"
                    searches_count += feature_store.redis_client.zcard(key)
                
                searches_normalized = searches_count * (config.DRIVER_TTL_SEC / config.SEARCH_TTL_SEC)
                ds_ratio = searches_normalized / max(drivers_in_isochrone, 0.5)
                
                proposed_price, surge_bonus, payout_formula = compute_surge_price(ds_ratio)
                
                prev_price_record = get_latest_price(h3_cell)
                previous_price = prev_price_record["price"] if prev_price_record else None
                
                final_price, bre_explanation = BusinessRulesEngine.apply_rules(
                    h3_cell=h3_cell,
                    proposed_price=proposed_price,
                    previous_price=previous_price
                )
                explanation = f"Fallback Level 2 (Node k-Ring). {bre_explanation}"
                fallback_level = 1
                
            # ==========================================
            # УРОВЕНЬ 3: Плоский H3-расчет (MVP 2.0)
            # ==========================================
            except Exception as e2:
                print(f"[API] [WARN] Расчет Уровня 2 не удался ({e2}). Откат на Уровень 3.")
                features = feature_store.get_features(h3_cell)
                ds_ratio = features["demand_supply_ratio"]
                
                proposed_price, surge_bonus, payout_formula = compute_surge_price(ds_ratio)
                
                prev_price_record = get_latest_price(h3_cell)
                previous_price = prev_price_record["price"] if prev_price_record else None
                
                final_price, bre_explanation = BusinessRulesEngine.apply_rules(
                    h3_cell=h3_cell,
                    proposed_price=proposed_price,
                    previous_price=previous_price
                )
                explanation = f"Fallback Level 3 (Flat H3). {bre_explanation}"
                fallback_level = 2

        # Рассчитываем Surge множитель
        surge_multiplier = round(final_price / base_fare, 2)
        
        # Сохранение цены и Outbox
        save_price_with_outbox(
            h3_index=h3_cell,
            price=final_price,
            base_price=base_fare,
            surge_multiplier=surge_multiplier,
            explanation=f"[{fallback_level}] {explanation}",
            test_group=test_group,
            surge_bonus=surge_bonus,
            payout_formula=payout_formula
        )
        
        return PriceResponse(
            h3_index=h3_cell,
            price=final_price,
            base_price=base_fare,
            surge_multiplier=surge_multiplier,
            explanation=explanation,
            is_fail_static=False,
            node_id=node_id,
            test_group=test_group,
            surge_bonus=surge_bonus,
            payout_formula=payout_formula
        )
        
    except Exception as e:
        # ==========================================
        # УРОВЕНЬ 4: Фолбек на базовый тариф (Fail-Static)
        # ==========================================
        print(f"[API] [WARN] Fail-Static сработал (Уровень 4): {e}")
        explanation = f"Fallback Level 4 (Fail-Static). DB/Redis/ML error: {e}"
        
        fallback_h3 = h3_cell if h3_cell else "unknown_h3"
        try:
            save_price_with_outbox(
                h3_index=fallback_h3,
                price=base_fare,
                base_price=base_fare,
                surge_multiplier=1.0,
                explanation=explanation,
                test_group=test_group,
                surge_bonus=0.0,
                payout_formula=f"{round(base_fare, 1)} (Fail-Static)"
            )
        except Exception as db_err:
            print(f"[API] [ERROR] Не удалось записать лог сбоя в БД: {db_err}")
            
        return PriceResponse(
            h3_index=fallback_h3,
            price=base_fare,
            base_price=base_fare,
            surge_multiplier=1.0,
            explanation=explanation,
            is_fail_static=True,
            node_id=node_id,
            test_group=test_group,
            surge_bonus=0.0,
            payout_formula=f"{round(base_fare, 1)} (Fail-Static)"
        )

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
        )

@app.post("/api/v1/virtual_hour", status_code=status.HTTP_200_OK)
def set_virtual_hour(payload: dict):
    """Устанавливает текущее время симуляции для расчета заторов."""
    hour = payload.get("hour", 12.0)
    feature_store.set_sim_virtual_hour(hour)
    return {"status": "success", "virtual_hour": hour}

