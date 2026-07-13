import time
import json
import threading
import queue
from typing import List, Dict, Any, Callable
from src.data.database import get_db_connection
from src import config

# Глобальная очередь для симуляции шины событий в памяти (при отключенном Redis)
in_memory_event_bus = queue.Queue()

class OutboxWorker(threading.Thread):
    """
    Фоновый воркер, опрашивающий таблицу price_outbox и транслирующий
    события изменения цен в шину (Redis Pub/Sub или in-memory очередь).
    Это симуляция CDC-коннектора (например, Debezium).
    """
    def __init__(self, pubsub_client=None):
        super().__init__()
        self.pubsub_client = pubsub_client
        self.daemon = True
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        print("[Outbox Worker] СDС воркер запущен.")
        while not self._stop_event.is_set():
            try:
                self._process_pending_events()
            except Exception as e:
                print(f"[Outbox Worker] Ошибка обработки: {e}")
            time.sleep(0.1)

    def _process_pending_events(self):
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            
            # Читаем необработанные события
            cursor.execute(
                """
                SELECT * FROM price_outbox 
                WHERE status = 'PENDING' 
                ORDER BY id ASC
                """
            )
            rows = cursor.fetchall()
            
            if not rows:
                return

            for row in rows:
                event_id = row['id']
                event_data = {
                    "event_id": event_id,
                    "price_id": row['price_id'],
                    "h3_index": row['h3_index'],
                    "price": row['price'],
                    "event_type": row['event_type'],
                    "created_at": row['created_at']
                }
                
                # Публикуем событие в шину данных
                self._publish_event(event_data)
                
                # Помечаем событие как обработанное в БД
                cursor.execute(
                    """
                    UPDATE price_outbox 
                    SET status = 'PROCESSED' 
                    WHERE id = ?
                    """,
                    (event_id,)
                )
            
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    def _publish_event(self, event_data: Dict[str, Any]):
        message = json.dumps(event_data)
        
        # Симулируем отправку в Redis Pub/Sub, если клиент передан
        if self.pubsub_client:
            try:
                self.pubsub_client.publish("dpe_price_updates", message)
                return
            except Exception:
                pass # Если Redis упал, пишем в in-memory очередь
                
        # Фолбек на очередь в памяти
        in_memory_event_bus.put(event_data)
