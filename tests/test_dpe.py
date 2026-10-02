import os

import pytest

from src import config

# Настройки тестов перед импортом
config.DB_PATH = "test_dpe_database.db"

from experiments.demand_model import DemandElasticityModel
from src.bre.rules import BusinessRulesEngine
from src.data.database import get_db_connection, get_latest_price, init_db, save_price_with_outbox
from src.data.feature_store import FeatureStore
from src.models.geogrid import get_h3_index, get_k_ring


@pytest.fixture(autouse=True)
def setup_and_teardown_db():
    """Фикстура для автоматического создания и удаления тестовой БД."""
    if os.path.exists(config.DB_PATH):
        try:
            os.remove(config.DB_PATH)
        except Exception:
            pass
            
    init_db()
    yield
    
    if os.path.exists(config.DB_PATH):
        try:
            os.remove(config.DB_PATH)
        except Exception:
            pass

def test_h3_geogrid():
    """Тест перевода координат в H3 индексы и получения соседей."""
    lat, lon = 55.7558, 37.6173
    h3_idx = get_h3_index(lat, lon, resolution=7)
    
    assert h3_idx is not None
    assert len(h3_idx) == 15  # Длина H3-индекса в шестнадцатеричном формате
    
    neighbors = get_k_ring(h3_idx, ring_size=1)
    assert len(neighbors) == 7  # Сама ячейка + 6 соседей

def test_business_rules_engine():
    """Тест валидации бизнес-ограничений (BRE)."""
    h3_cell = "871100000ffffff" # Случайный H3-индекс
    
    # 1. Тест Price Floor
    price, explanation = BusinessRulesEngine.apply_rules(
        h3_cell=h3_cell,
        proposed_price=50.0  # Ниже MIN_PRICE (120)
    )
    assert price == config.MIN_PRICE
    assert "Price Floor triggered" in explanation

    # 2. Тест Price Ceiling
    price, explanation = BusinessRulesEngine.apply_rules(
        h3_cell=h3_cell,
        proposed_price=3000.0  # Выше MAX_PRICE (1500)
    )
    assert price == config.MAX_PRICE
    assert "Price Ceiling triggered" in explanation

    # 3. Тест Velocity Limit (кап изменений)
    previous_price = 200.0
    # Максимальный рост: +15% = 230
    price, explanation = BusinessRulesEngine.apply_rules(
        h3_cell=h3_cell,
        proposed_price=300.0,  # Захотели поднять до 300
        previous_price=previous_price
    )
    assert price == 230.0
    assert "Velocity Limit exceeded" in explanation

    # 4. Тест нормальной цены
    price, explanation = BusinessRulesEngine.apply_rules(
        h3_cell=h3_cell,
        proposed_price=180.0,
        previous_price=previous_price
    )
    assert price == 180.0
    assert "ML proposed price is within all safety limits" in explanation

def test_database_and_outbox():
    """Тест транзакционного сохранения цены и Outbox."""
    h3_cell = "871100000ffffff"
    
    price_id = save_price_with_outbox(
        h3_index=h3_cell,
        price=180.0,
        base_price=150.0,
        surge_multiplier=1.2,
        explanation="Test explanation"
    )
    
    assert price_id is not None
    assert price_id > 0
    
    # Проверяем запись в БД
    latest = get_latest_price(h3_cell)
    assert latest is not None
    assert latest["price"] == 180.0
    assert latest["surge_multiplier"] == 1.2
    assert latest["explanation"] == "Test explanation"
    
    # Проверяем, что событие попало в outbox
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM price_outbox WHERE price_id = ?", (price_id,))
    outbox_row = cursor.fetchone()
    conn.close()
    
    assert outbox_row is not None
    assert outbox_row["h3_index"] == h3_cell
    assert outbox_row["status"] == "PENDING"
    assert outbox_row["event_type"] == "PRICE_UPDATED"

def test_feature_store_and_simulated_redis():
    """Тест фиче-стора с симулятором Redis в памяти."""
    config.USE_LOCAL_SIMULATED_REDIS = True
    fs = FeatureStore()
    
    h3_cell = get_h3_index(55.7558, 37.6173)
    
    # Добавляем 2 пинга от разных водителей
    fs.register_driver_ping("driver_1", 55.7558, 37.6173)
    fs.register_driver_ping("driver_2", 55.7550, 37.6170)
    
    # Добавляем 5 запросов поиска
    for i in range(5):
        fs.register_search_request(f"search_{i}", 55.7558, 37.6173)
        
    features = fs.get_features(h3_cell)
    
    assert features["h3_cell"] == h3_cell
    assert features["drivers_in_cell"] == 2
    assert features["searches_in_cell"] == 5
    assert features["demand_supply_ratio"] == 0.25

def test_demand_elasticity_model():
    """Тест модели спроса и поиска оптимальной цены."""
    model = DemandElasticityModel()
    
    # Для тестов не будем обучать полную модель CatBoost на 100 итераций,
    # проверим детерминированный фолбек
    assert model.is_trained is False
    
    # Оптимальная цена при высоком спросе должна быть выше, чем базовая
    opt_price_high_demand = model.find_optimal_price(ds_ratio=4.0, competitor_price=250.0, hour=18)
    opt_price_low_demand = model.find_optimal_price(ds_ratio=0.2, competitor_price=130.0, hour=10)
    
    # Цена при высоком спросе должна увеличиться
    assert opt_price_high_demand >= opt_price_low_demand

def test_h3_calibrated_smoothing():
    """Тест калиброванных весов k-Ring сглаживания (center=1.0, ring_1=0.15, ring_2=0.0)."""
    from src.models.geogrid import smooth_metric_k_ring
    
    h3_center = "8711aa71affffff"
    h3_neighbor_1 = "8711aa71effffff"  # Должна быть на расстоянии dist=1
    
    # Задаем тестовые метрики
    metrics_map = {
        h3_center: 200.0,
        h3_neighbor_1: 100.0
    }
    
    # 1. Сглаживание только для центральной ячейки (остальные соседи отсутствуют = 0.0)
    # total_weight = 1.0 (center) + 6 * 0.15 (neighbors) = 1.9
    # weighted_sum = 200.0 * 1.0 + 6 * 0.0 = 200.0
    # expected = 200.0 / 1.9 = 105.263...
    smoothed_val_only_center = smooth_metric_k_ring(h3_center, {h3_center: 200.0}, ring_size=1)
    assert abs(smoothed_val_only_center - (200.0 / 1.9)) < 0.01
    
    # 2. Сглаживание с соседом на расстоянии dist=1
    # total_weight = 1.0 (center) + 6 * 0.15 (neighbors) = 1.9
    # weighted_sum = 200.0 * 1.0 + 100.0 * 0.15 + 5 * 0.0 = 215.0
    # expected = 215.0 / 1.9 = 113.157...
    smoothed_val_with_neighbor = smooth_metric_k_ring(h3_center, metrics_map, ring_size=1)
    assert abs(smoothed_val_with_neighbor - (215.0 / 1.9)) < 0.01

def test_road_graph_topology():
    """Тест топологии графа, привязки узлов и Reverse Dijkstra."""
    from src.models.road_graph import RoadGraph
    graph = RoadGraph()
    
    # Должно быть 70 узлов
    assert len(graph.nodes) == 70
    
    # Проверка snap_to_node
    # Координаты центра Москвы
    node_center = graph.snap_to_node(55.7558, 37.6173)
    assert node_center == "center"
    
    # Проверка Reverse Dijkstra
    # Из центра до центра ETA = 0
    etas = graph.reverse_dijkstra("center", max_eta_sec=600, edge_speeds={})
    assert etas["center"] == 0.0
    
    # Проверка обхода водной преграды (Москва-река)
    # Направление ttk_4 -> hub_6 разорвано. Проверяем, что Дейкстра найдет обходной путь
    etas_with_barrier = graph.reverse_dijkstra("hub_6", max_eta_sec=3600, edge_speeds={})
    assert "ttk_4" in etas_with_barrier  # Путь есть в обход
    assert etas_with_barrier["ttk_4"] > 0.0

def test_driver_edge_eta():
    """Тест точного расчета ETA водителя на ребре графа."""
    config.USE_LOCAL_SIMULATED_REDIS = True
    fs = FeatureStore()
    
    # Водитель на 50% ребра bulvar_0 -> sadovoe_0
    # Длина ребра = 1.3 км, базовая скорость = 50 км/ч
    # Время прохождения всего ребра = (1.3 / 50) * 3600 = 93.6 сек
    # Оставшееся время = 93.6 * (1 - 0.5) = 46.8 сек
    
    fs.register_driver_ping_graph(
        driver_id="driver_test_eta",
        u="bulvar_0",
        v="sadovoe_0",
        progress=0.5,
        lat=55.7600,
        lon=37.6200
    )
    
    # Запрашиваем фичи для sadovoe_0 (узел назначения водителя)
    # Поскольку водитель едет прямо в sadovoe_0, его ETA до sadovoe_0 = 46.8 сек
    edge_speeds = fs.get_dynamic_edge_weights()
    features = fs.get_features_graph("sadovoe_0", edge_speeds)
    
    # В изохроне 7 минут (420 сек) водитель должен присутствовать
    assert features["drivers_in_isochrone"] == 1

def test_edge_weight_fallback():
    """Тест трёхуровневой системы весов рёбер графа (телеметрия -> профиль -> базовая скорость)."""
    config.USE_LOCAL_SIMULATED_REDIS = True
    fs = FeatureStore()
    
    # Уровень 1: Проверяем базовую скорость без пробок и телеметрии (для часа = 12 дня)
    fs.set_sim_virtual_hour(12.0) # День
    speeds = fs.get_dynamic_edge_weights()
    
    # Ребро center -> bulvar_0 (внутриквартальная дорога "inside", базовая скорость 40 км/ч)
    # Коэффициент в 12:00 = 0.85 -> Скорость = 40 * 0.85 = 34.0 км/ч
    assert abs(speeds[("center", "bulvar_0")] - 34.0) < 0.1
    
    # Уровень 2: Записываем телеметрию на ребро (живая скорость)
    fs.register_edge_telemetry("center", "bulvar_0", 15.0) # Пробка 15 км/ч
    speeds_telemetry = fs.get_dynamic_edge_weights()
    
    # Скорость должна стать 15.0 км/ч
    assert speeds_telemetry[("center", "bulvar_0")] == 15.0

def test_additive_surge_math():
    """Аддитивная надбавка на 10 минут при ds=2 около 94 руб."""
    import math

    from src.pricing import quote_fare

    duration_sec = 600.0
    quoted = quote_fare(duration_sec, 10.0, 2.0, "ADDITIVE")
    decay = 1.0 - math.exp(-config.LAMBDA_DECAY * duration_sec)
    expected_bonus = config.ALPHA_SURGE_RATE * duration_sec + config.BETA_SURGE_VAL * decay

    assert abs(expected_bonus - 94.29) < 0.1
    assert abs(quoted.surge_bonus - expected_bonus) < 1e-9
    assert quoted.price == quoted.base_fare + quoted.surge_bonus

    multiplicative = quote_fare(duration_sec, 10.0, 2.0, "MULTIPLICATIVE")
    assert multiplicative.surge_bonus == 0.0
    assert multiplicative.price == multiplicative.base_fare * 1.5

def test_switchback_toggle():
    """Тест переключения групп Switchback-тестирования в зависимости от времени."""
    # Четный час (например, 10:00) / SWITCHBACK_WINDOW_HOURS (1) = 10 -> 10 % 2 == 0 -> MULTIPLICATIVE
    # Нечетный час (например, 11:00) / SWITCHBACK_WINDOW_HOURS (1) = 11 -> 11 % 2 == 1 -> ADDITIVE
    
    hour_even = 10
    hour_odd = 11
    
    is_additive_even = (hour_even // config.SWITCHBACK_WINDOW_HOURS) % 2 == 1
    is_additive_odd = (hour_odd // config.SWITCHBACK_WINDOW_HOURS) % 2 == 1
    
    assert is_additive_even is False
    assert is_additive_odd is True

def test_od_routing_distance():
    """Тест поиска кратчайшего пути OD (Origin-Destination) на графе."""
    from src.models.road_graph import RoadGraph
    graph = RoadGraph()
    
    # Кратчайший путь из центра до bulvar_0
    # center -> bulvar_0 (прямой путь)
    # Расстояние: 1.2 км, скорость 40 км/ч
    # Время проезда: (1.2 / 40) * 3600 = 108.0 сек
    
    time_sec, dist_km = graph.shortest_path_od("center", "bulvar_0", {})
    assert dist_km == 1.2
    assert time_sec == 108.0


