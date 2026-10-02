import os

# Базовые настройки путей
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.getenv("DB_PATH", os.path.join(BASE_DIR, "dpe_database.db"))

# Настройки гео-распределения H3
H3_RESOLUTION: int = 7  # Размер ячейки ~1.2 км
K_RING_RADIUS: int = 1  # Радиус сглаживания цен k-Ring

# Бизнес-правила ценообразования (BRE Constraints)
BASE_PRICE: float = 150.0       # Базовый тариф (surge 1.0x)
MIN_PRICE: float = 120.0        # Абсолютный минимум (floor)
MAX_PRICE: float = 1500.0       # Абсолютный максимум (ceiling)
MAX_PRICE_CHANGE_PCT: float = 0.15  # Лимит изменения цены за цикл пересчета (15%)

# Параметры тарифа (Базовая стоимость проезда)
RATE_PER_SECOND: float = 0.15      # Стоимость секунды поездки (~9 руб/мин)
RATE_PER_KILOMETER: float = 12.0    # Стоимость километра поездки (~12 руб/км)
MIN_FARE: float = 150.0            # Минимальная стоимость поездки (руб)

# Параметры аддитивного сурджа (Additive Surge)
ALPHA_SURGE_RATE: float = 0.10     # Добавочный тариф в сек на единицу превышения спроса (руб/сек)
BETA_SURGE_VAL: float = 100.0      # Балансирующий коэффициент (руб)
LAMBDA_DECAY: float = 0.0007       # Интенсивность затухания сурджа (вероятность окончания за секунду)

# Параметры Switchback тестирования
SWITCHBACK_WINDOW_HOURS: int = 1   # Окно переключения тестовых групп (в часах)

# Параметры очистки данных ("Zombie Drivers")
DRIVER_TTL_SEC: int = 30         # Водитель считается неактивным через 30 секунд
SEARCH_TTL_SEC: int = 300        # Поисковый запрос устаревает через 5 минут

# Параметры дорожного графа (MVP 3.0)
MAX_ETA_SEC: int = 420           # Порог изохроны предложения в секундах (7 минут)
EDGE_TELEMETRY_WINDOW_SEC: int = 300  # Окно агрегации динамических скоростей ребер (5 минут)

# Исторические профили заторов для разных типов дорог (congestion factors)
# Формат: {road_type: {hour_range: factor}}
CONGESTION_PROFILES = {
    "mkad": {
        (7, 10): 0.45,
        (10, 17): 0.70,
        (17, 20): 0.40,
        (20, 7): 0.95
    },
    "ttk": {
        (7, 10): 0.50,
        (10, 17): 0.75,
        (17, 20): 0.45,
        (20, 7): 0.95
    },
    "sadovoe": {
        (7, 10): 0.40,
        (10, 17): 0.60,
        (17, 20): 0.35,
        (20, 7): 0.90
    },
    "radial": {
        (7, 10): 0.40,
        (10, 17): 0.65,
        (17, 20): 0.35,
        (20, 7): 0.95
    },
    "inside": {
        (7, 10): 0.80,
        (10, 17): 0.85,
        (17, 20): 0.75,
        (20, 7): 0.95
    }
}

# Настройки Redis
REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT: int = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB: int = 0
# Локально и в тестах Redis не нужен. Docker Compose ставит false и REDIS_URL.
USE_LOCAL_SIMULATED_REDIS: bool = os.getenv("USE_LOCAL_SIMULATED_REDIS", "true").lower() in (
    "1",
    "true",
    "yes",
)

# Настройки интеграции Causal Engine
CAUSAL_ENGINE_URL: str = os.getenv("CAUSAL_ENGINE_URL", "http://localhost:8100")
CAUSAL_ENGINE_TIMEOUT_SEC: float = float(os.getenv("CAUSAL_ENGINE_TIMEOUT_SEC", "0.2"))
CAUSAL_UPLIFT_THRESHOLD: float = float(os.getenv("CAUSAL_UPLIFT_THRESHOLD", "0.05"))
CAUSAL_ENABLED: bool = os.getenv("CAUSAL_ENABLED", "true").lower() in ("true", "1", "yes")

# Параметры симуляции
SIM_TOWN_CENTER_LAT: float = 55.7558  # Центр симуляции (Москва)
SIM_TOWN_CENTER_LON: float = 37.6173
SIM_RADIUS_KM: float = 18.0
# Флаг симуляции аварии (Fault Injection)
FAULT_INJECTION_ACTIVE: bool = False
# Ручки симулятора: virtual_hour, inject_fault, телеметрия рёбер. В обычном API их нет.
SIM_MODE: bool = os.getenv("SIM_MODE", "false").lower() in ("1", "true", "yes")
