import sqlite3
from typing import Any, Dict, Optional

from src import clock, config


def get_db_connection() -> sqlite3.Connection:
    """Возвращает соединение с базой данных SQLite."""
    conn = sqlite3.connect(config.DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Создает необходимые таблицы, если они не существуют, и проводит легкую миграцию."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Таблица цен
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                h3_index TEXT NOT NULL,
                price REAL NOT NULL,
                base_price REAL NOT NULL,
                surge_multiplier REAL NOT NULL,
                explanation TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        
        # Миграция: Добавление новых полей в таблицу prices, если их нет
        try:
            cursor.execute("ALTER TABLE prices ADD COLUMN test_group TEXT DEFAULT 'MULTIPLICATIVE'")
        except sqlite3.OperationalError:
            pass # Столбец уже существует
            
        try:
            cursor.execute("ALTER TABLE prices ADD COLUMN surge_bonus REAL DEFAULT 0.0")
        except sqlite3.OperationalError:
            pass
            
        try:
            cursor.execute("ALTER TABLE prices ADD COLUMN payout_formula TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        
        # Индексы для быстрого поиска
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_prices_h3 ON prices(h3_index)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_prices_created ON prices(created_at)")
        
        # Таблица Transactional Outbox
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS price_outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                price_id INTEGER NOT NULL,
                h3_index TEXT NOT NULL,
                price REAL NOT NULL,
                event_type TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('PENDING', 'PROCESSED')),
                created_at REAL NOT NULL,
                FOREIGN KEY (price_id) REFERENCES prices(id)
            )
        """)
        
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_outbox_status ON price_outbox(status)")
        
        # Таблица аналитики симуляции (метрики по принятию заказов)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS simulation_analytics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                test_group TEXT NOT NULL,
                trip_id TEXT NOT NULL,
                distance_km REAL NOT NULL,
                duration_sec REAL NOT NULL,
                price REAL NOT NULL,
                surge_bonus REAL NOT NULL,
                accepted INTEGER NOT NULL, -- 1 или 0
                driver_utility REAL NOT NULL,
                driver_id TEXT NOT NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sim_group ON simulation_analytics(test_group)")
        
        conn.commit()

def save_price_with_outbox(
    h3_index: str,
    price: float,
    base_price: float,
    surge_multiplier: float,
    explanation: str,
    test_group: str = "MULTIPLICATIVE",
    surge_bonus: float = 0.0,
    payout_formula: str = ""
) -> int:
    """
    Транзакционно сохраняет цену в таблицу prices и записывает
    событие изменения цены в таблицу price_outbox.
    """
    created_at = clock.now()
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        # Начинаем транзакцию
        cursor.execute("BEGIN TRANSACTION")
        
        # 1. Запись в таблицу prices
        cursor.execute(
            """
            INSERT INTO prices (
                h3_index, price, base_price, surge_multiplier, explanation, created_at,
                test_group, surge_bonus, payout_formula
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (h3_index, price, base_price, surge_multiplier, explanation, created_at,
             test_group, surge_bonus, payout_formula)
        )
        price_id = cursor.lastrowid
        
        # 2. Запись в таблицу outbox
        cursor.execute(
            """
            INSERT INTO price_outbox (price_id, h3_index, price, event_type, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (price_id, h3_index, price, "PRICE_UPDATED", "PENDING", created_at)
        )
        
        # Коммит транзакции
        conn.commit()
        return price_id
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def get_latest_price(h3_index: str) -> Optional[Dict[str, Any]]:
    """Возвращает последнюю актуальную цену для H3 ячейки."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM prices 
            WHERE h3_index = ? 
            ORDER BY created_at DESC LIMIT 1
            """,
            (h3_index,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

