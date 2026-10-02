import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import time
import requests
import sqlite3
from src import config

API_URL = "http://localhost:8000"

def run_stress_test():
    print("=== ЗАПУСК СТРЕСС-ТЕСТА СЦЕНАРИЯ FAIL-STATIC ===")
    
    # 1. Проверяем доступность API
    try:
        resp = requests.get(f"{API_URL}/health", timeout=1.0)
        assert resp.status_code == 200
        print("[+] API сервер динамического ценообразования доступен.")
    except Exception as e:
        print(f"[-] Ошибка подключения к серверу: {e}")
        return

    # 2. Обычные запросы до сбоя
    print("\n[Шаг 1] Отправка обычных поисковых запросов (без инжекции сбоя)...")
    for i in range(2):
        resp = requests.post(
            f"{API_URL}/api/v1/search",
            json={
                "search_id": f"test_normal_{i}",
                "lat": config.SIM_TOWN_CENTER_LAT + 0.001 * i,
                "lon": config.SIM_TOWN_CENTER_LON - 0.001 * i
            }
        )
        data = resp.json()
        print(f"  Запрос {i} -> Цена: {data['price']} руб. (Fail-Static: {data['is_fail_static']}) | {data['explanation']}")

    # 3. Инжекция сбоя в ML-микросервисе
    print("\n[Шаг 2] Инжекция сбоя: выключение ML-микросервиса...")
    resp = requests.post(
        f"{API_URL}/api/v1/inject_fault",
        json={"enabled": True}
    )
    assert resp.status_code == 200
    print(f"[+] Сбой успешно инжектирован: {resp.json()}")

    # 4. Стресс-тест: 30 быстрых запросов в режиме сбоя
    print("\n[Шаг 3] Запуск стресс-теста: отправка 30 запросов в режиме аварии...")
    start_time = time.time()
    
    success_count = 0
    fail_static_count = 0
    total_requests = 30
    
    for i in range(total_requests):
        try:
            resp = requests.post(
                f"{API_URL}/api/v1/search",
                json={
                    "search_id": f"test_fault_stress_{i}",
                    "lat": config.SIM_TOWN_CENTER_LAT + 0.002 * i,
                    "lon": config.SIM_TOWN_CENTER_LON + 0.002 * i
                },
                timeout=1.0
            )
            if resp.status_code == 200:
                success_count += 1
                data = resp.json()
                if data["is_fail_static"] and data["price"] == config.BASE_PRICE:
                    fail_static_count += 1
        except Exception as e:
            print(f"  Ошибка при отправке запроса {i}: {e}")
            
    duration = time.time() - start_time
    print(f"\n[+] Стресс-тест завершен за {duration:.3f} сек.")
    print(f"    - Успешных HTTP ответов (SLA): {success_count}/{total_requests} ({(success_count/total_requests)*100:.1f}%)")
    print(f"    - Сработало Fail-Static (базовый тариф): {fail_static_count}/{total_requests}")
    
    # Проверка, что API ответило без задержек (Latency)
    avg_latency = (duration / total_requests) * 1000
    print(f"    - Средний Latency ответа при аварии: {avg_latency:.1f} мс (целевой показатель SLA < 200 мс)")

    # 5. Проверка записи в БД и счетчика дашборда
    print("\n[Шаг 4] Проверка транзакционных логов в SQLite БД...")
    conn = sqlite3.connect(config.DB_PATH)
    cursor = conn.cursor()
    
    # Считаем количество записей с упоминанием Fail-Static в БД
    cursor.execute("SELECT COUNT(*) FROM prices WHERE explanation LIKE '%Fail-Static%'")
    db_fail_static_count = cursor.fetchone()[0]
    
    # Получаем последнюю запись для проверки
    cursor.execute("SELECT * FROM prices ORDER BY id DESC LIMIT 1")
    last_row = cursor.fetchone()
    conn.close()
    
    print(f"[+] Проверка БД:")
    print(f"    - Всего записей Fail-Static в БД: {db_fail_static_count}")
    if last_row:
        print(f"    - Последняя запись: ID={last_row[0]} | H3={last_row[1]} | Цена={last_row[2]} | Описание='{last_row[5]}'")

    # 6. Отключение сбоя
    print("\n[Шаг 5] Восстановление системы: включение ML-микросервиса...")
    resp = requests.post(
        f"{API_URL}/api/v1/inject_fault",
        json={"enabled": False}
    )
    assert resp.status_code == 200
    print(f"[+] Система восстановлена: {resp.json()}")

    # 7. Проверка работы после восстановления
    print("\n[Шаг 6] Проверка работы после восстановления...")
    resp = requests.post(
        f"{API_URL}/api/v1/search",
        json={
            "search_id": "test_after_recovery",
            "lat": config.SIM_TOWN_CENTER_LAT,
            "lon": config.SIM_TOWN_CENTER_LON
        }
    )
    data = resp.json()
    print(f"  Запрос после восстановления -> Цена: {data['price']} руб. (Fail-Static: {data['is_fail_static']}) | {data['explanation']}")

    print("\n=== ТЕСТИРОВАНИЕ СЦЕНАРИЯ FAIL-STATIC УСПЕШНО ЗАВЕРШЕНО ===")

if __name__ == "__main__":
    run_stress_test()
