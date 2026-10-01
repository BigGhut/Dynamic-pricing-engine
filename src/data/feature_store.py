import os
import time
from typing import Any, Dict, List, Optional

import redis

from src import clock, config
from src.models.geogrid import get_k_ring
from src.models.road_graph import RoadGraph
from src.models.speeds import congestion_factor


# Заглушка для имитации Redis в памяти на случай отсутствия подключения к настоящему Redis
class SimulatedRedis:
    def __init__(self):
        self.store = {}  # key -> val
        self.ttls = {}   # key -> expire_timestamp
        self.sets = {}   # key -> dict(member -> timestamp)
        self._cleaned_at = None

    def _clean_expired(self):
        now = clock.now()
        # Reads in one quote share a timestamp. Expiry only changes when the clock does.
        if self._cleaned_at == now:
            return
        self._cleaned_at = now
        # Чистим обычные ключи
        expired_keys = [k for k, exp in self.ttls.items() if now > exp]
        for k in expired_keys:
            self.store.pop(k, None)
            self.ttls.pop(k, None)
            
        # Чистим Sorted Sets (ZSET) по бакетам
        # Удаляем бакеты старше 15 минут (900 секунд)
        for key in list(self.sets.keys()):
            parts = key.split(":")
            if len(parts) >= 5 and (parts[0] == "h3" or parts[0] == "node") and parts[2] == "ts":
                try:
                    ts_bucket = int(parts[3])
                    if now - ts_bucket > 900:  # TTL бакета 15 минут
                        self.sets.pop(key, None)
                        continue
                except ValueError:
                    pass
            
            # Также чистим старые элементы внутри активного ZSET
            if key in self.sets:
                members = self.sets[key]
                ttl = config.DRIVER_TTL_SEC if "drivers" in key else config.SEARCH_TTL_SEC
                expired_members = [m for m, ts in members.items() if now - ts > ttl]
                for m in expired_members:
                    members.pop(m, None)

    def zadd(self, name: str, mapping: Dict[str, float]):
        self._clean_expired()
        if name not in self.sets:
            self.sets[name] = {}
        for member, score in mapping.items():
            self.sets[name][member] = score

    def zcard(self, name: str) -> int:
        self._clean_expired()
        if name not in self.sets:
            return 0
        return len(self.sets[name])

    def zrangebyscore(self, name: str, min_score: float, max_score: float) -> List[str]:
        self._clean_expired()
        if name not in self.sets:
            return []
        members = self.sets[name]
        return [m for m, score in members.items() if min_score <= score <= max_score]

    def zremrangebyscore(self, name: str, min_score: float, max_score: float):
        if name not in self.sets:
            return
        members = self.sets[name]
        to_remove = [m for m, score in members.items() if min_score <= score <= max_score]
        for m in to_remove:
            members.pop(m, None)

    def set(self, name: str, value: Any, ex: Optional[int] = None):
        self._clean_expired()
        self.store[name] = value
        if ex:
            self.ttls[name] = clock.now() + ex

    def get(self, name: str) -> Optional[Any]:
        self._clean_expired()
        if name in self.store:
            if name in self.ttls and clock.now() > self.ttls[name]:
                self.store.pop(name, None)
                self.ttls.pop(name, None)
                return None
            return self.store[name]
        return None

class FeatureStore:
    def __init__(self):
        self.redis_client = None
        self.simulated = False
        self.graph = RoadGraph()
        
        if not config.USE_LOCAL_SIMULATED_REDIS:
            self.redis_client = None
            last_error = None
            for _attempt in range(10):
                try:
                    redis_url = os.getenv("REDIS_URL", "").strip()
                    if redis_url:
                        client = redis.Redis.from_url(
                            redis_url, decode_responses=True, socket_connect_timeout=1
                        )
                    else:
                        client = redis.Redis(
                            host=config.REDIS_HOST,
                            port=config.REDIS_PORT,
                            db=config.REDIS_DB,
                            decode_responses=True,
                            socket_connect_timeout=1,
                        )
                    client.ping()
                    self.redis_client = client
                    print("[Feature Store] Успешное подключение к Redis.")
                    break
                except Exception as error:
                    last_error = error
                    time.sleep(0.5)
            if self.redis_client is None:
                print(f"[Feature Store] Подключение к Redis не удалось ({last_error}). Включена симуляция в памяти.")
                self.redis_client = SimulatedRedis()
                self.simulated = True
        else:
            print("[Feature Store] Включена симуляция Redis в памяти (конфигурационный флаг).")
            self.redis_client = SimulatedRedis()
            self.simulated = True

    def register_driver_ping(self, driver_id: str, lat: float, lon: float):
        """
        Регистрирует координаты водителя.
        Для совместимости с H3 и автоматического снейпинга на граф.
        """
        node_id = self.graph.snap_to_node(lat, lon)
        # При снейпинге считаем, что водитель стоит на входящем ребре в этот узел
        self.register_driver_ping_graph(driver_id, "center", node_id, 1.0, lat, lon)

    def register_driver_ping_graph(self, driver_id: str, u: str, v: str, progress: float, lat: float, lon: float):
        """
        Регистрирует координаты водителя на ребре графа (u, v) с прогрессом progress.
        Поддерживает двойную индексацию (запись в бакеты node и h3).
        """
        now = clock.now()
        ts_bucket = int(now // 60) * 60
        
        # 1. Запись в графовый бакет узла притяжения (узла назначения v)
        node_key = f"node:{v}:ts:{ts_bucket}:drivers"
        # Сохраняем строку вида "driver_id:u:v:progress"
        member = f"{driver_id}:{u}:{v}:{progress}"
        self.redis_client.zadd(node_key, {member: now})
        
        # 2. Двойная запись в H3 бакет для обратной совместимости
        h3_cell = self.graph.nodes[v]["h3_cell"]
        h3_key = f"h3:{h3_cell}:ts:{ts_bucket}:drivers"
        self.redis_client.zadd(h3_key, {driver_id: now})

    def register_search_request(self, search_id: str, lat: float, lon: float) -> str:
        """
        Регистрирует поисковый запрос.
        Снейпит координаты на граф и делает двойную запись (node + h3).
        """
        node_id = self.graph.snap_to_node(lat, lon)
        now = clock.now()
        ts_bucket = int(now // 60) * 60
        
        # 1. Запись в графовый бакет
        node_key = f"node:{node_id}:ts:{ts_bucket}:searches"
        self.redis_client.zadd(node_key, {search_id: now})
        
        # 2. Запись в H3 бакет для обратной совместимости
        h3_cell = self.graph.nodes[node_id]["h3_cell"]
        h3_key = f"h3:{h3_cell}:ts:{ts_bucket}:searches"
        self.redis_client.zadd(h3_key, {search_id: now})
        
        return node_id

    def set_competitor_price(self, node_id: str, price: float):
        """Устанавливает цену конкурента по ID узла (и дублирует по H3)."""
        key_node = f"node:{node_id}:competitor_price"
        self.redis_client.set(key_node, str(price), ex=300)
        
        if node_id in self.graph.nodes:
            h3_cell = self.graph.nodes[node_id]["h3_cell"]
            key_h3 = f"h3:{h3_cell}:competitor_price"
            self.redis_client.set(key_h3, str(price), ex=300)

    def register_edge_telemetry(self, u: str, v: str, speed_kmh: float):
        """Регистрирует скорость на ребре графа (u, v) с TTL 5 минут."""
        key = f"edge:{u}:{v}:speed"
        self.redis_client.set(key, float(speed_kmh), ex=config.EDGE_TELEMETRY_WINDOW_SEC)

    def set_sim_virtual_hour(self, virtual_hour: float):
        """Сохраняет виртуальный час симуляции в Redis для исторического fallback пробок."""
        self.redis_client.set("sim:virtual_hour", float(virtual_hour))

    def get_dynamic_edge_weights(self) -> Dict[tuple[str, str], float]:
        """
        Вычисляет динамические скорости для всех ребер графа на основе трёхуровневого fallback.
        Возвращает словарь {(u, v): speed_kmh}.
        """
        edge_speeds = {}
        
        # Считываем виртуальный час из Redis (или используем текущее системное время)
        vh_raw = self.redis_client.get("sim:virtual_hour")
        hour = int(float(vh_raw)) if vh_raw else time.localtime(clock.now()).tm_hour

        for u, neighbors in self.graph.adj.items():
            for v, edge_data in neighbors.items():
                key = f"edge:{u}:{v}:speed"
                speed_raw = self.redis_client.get(key)
                if speed_raw is not None:
                    speed = float(speed_raw)
                else:
                    speed = edge_data["base_speed_kmh"] * congestion_factor(edge_data["road_type"], hour)
                edge_speeds[(u, v)] = max(speed, 5.0)
                
        return edge_speeds

    def get_features(self, h3_cell: str) -> Dict[str, Any]:
        """
        Метод обратной совместимости по H3.
        Реализует старый плоский H3 расчет для каскадного Fail-Static.
        """
        now = clock.now()
        ts_bucket = int(now // 60) * 60

        active_drivers = set()
        buckets_for_drivers = [ts_bucket, ts_bucket - 60]
        neighbors = get_k_ring(h3_cell, config.K_RING_RADIUS)
        
        for cell in neighbors:
            for bucket in buckets_for_drivers:
                key = f"h3:{cell}:ts:{bucket}:drivers"
                drivers = self.redis_client.zrangebyscore(
                    key, now - config.DRIVER_TTL_SEC, now
                )
                active_drivers.update(drivers)
                
        total_drivers_k_ring = len(active_drivers)
        
        drivers_in_cell = set()
        for bucket in buckets_for_drivers:
            key = f"h3:{h3_cell}:ts:{bucket}:drivers"
            drivers = self.redis_client.zrangebyscore(key, now - config.DRIVER_TTL_SEC, now)
            drivers_in_cell.update(drivers)
        drivers_count = len(drivers_in_cell)

        searches_count = 0
        for i in range(5):
            bucket = ts_bucket - (i * 60)
            key = f"h3:{h3_cell}:ts:{bucket}:searches"
            searches_count += self.redis_client.zcard(key)

        comp_price_raw = self.redis_client.get(f"h3:{h3_cell}:competitor_price")
        competitor_price = float(comp_price_raw) if comp_price_raw else config.BASE_PRICE

        searches_normalized = searches_count * (config.DRIVER_TTL_SEC / config.SEARCH_TTL_SEC)
        supply = max(total_drivers_k_ring, 0.5)
        demand_supply_ratio = searches_normalized / supply

        return {
            "h3_cell": h3_cell,
            "drivers_in_cell": drivers_count,
            "drivers_in_k_ring": total_drivers_k_ring,
            "searches_in_cell": searches_count,
            "competitor_price": competitor_price,
            "demand_supply_ratio": demand_supply_ratio
        }

    def get_features_graph(self, node_id: str, edge_speeds: Dict[tuple[str, str], float]) -> Dict[str, Any]:
        """
        Вычисляет фичи предложения (с учётом точного ETA водителей в изохроне 7 минут)
        и спроса (за 5 минут на узле).
        """
        now = clock.now()
        ts_bucket = int(now // 60) * 60
        
        # 1. Запуск Reverse Dijkstra для определения изохроны
        # Находим все узлы, откуда можно доехать до node_id за ≤ 7 минут (420 сек)
        reachable_nodes_eta = self.graph.reverse_dijkstra(node_id, config.MAX_ETA_SEC, edge_speeds)
        
        # 2. Фильтрация и интерполяция водителей
        active_drivers_in_isochrone = set()
        buckets_for_drivers = [ts_bucket, ts_bucket - 60]
        
        # Для каждого достижимого узла в изохроне смотрим его водителей
        for v, path_eta in reachable_nodes_eta.items():
            for bucket in buckets_for_drivers:
                key = f"node:{v}:ts:{bucket}:drivers"
                # Достаем записи "driver_id:edge_u:edge_v:progress"
                members = self.redis_client.zrangebyscore(key, now - config.DRIVER_TTL_SEC, now)
                
                for member in members:
                    parts = member.split(":")
                    if len(parts) == 4:
                        driver_id, edge_u, edge_v, progress_str = parts
                        progress = float(progress_str)
                        
                        # Время доезда водителя до узла назначения ребра v (которое и есть edge_v)
                        # Так как водитель едет от edge_u к edge_v, ему осталось проехать (1 - progress) ребра
                        if edge_v == v:
                            dist = self.graph.adj[edge_u][edge_v]["distance_km"]
                            speed = edge_speeds.get((edge_u, edge_v), self.graph.adj[edge_u][edge_v]["base_speed_kmh"])
                            time_left_on_edge = ((dist * (1.0 - progress)) / max(speed, 5.0)) * 3600.0
                            
                            # Итоговый ETA водителя до целевого узла спроса node_id
                            total_eta = time_left_on_edge + path_eta
                            
                            if total_eta <= config.MAX_ETA_SEC:
                                active_drivers_in_isochrone.add(driver_id)
                                
        drivers_in_isochrone = len(active_drivers_in_isochrone)

        # 3. Расчет локального спроса за 5 минут на узле
        searches_count = 0
        for i in range(5):
            bucket = ts_bucket - (i * 60)
            key = f"node:{node_id}:ts:{bucket}:searches"
            searches_count += self.redis_client.zcard(key)

        # 4. Цена конкурента
        comp_price_raw = self.redis_client.get(f"node:{node_id}:competitor_price")
        competitor_price = float(comp_price_raw) if comp_price_raw else config.BASE_PRICE

        # 5. Коэффициент спроса
        searches_normalized = searches_count * (config.DRIVER_TTL_SEC / config.SEARCH_TTL_SEC)
        supply = max(drivers_in_isochrone, 0.5)
        demand_supply_ratio = searches_normalized / supply

        return {
            "node_id": node_id,
            "drivers_in_isochrone": drivers_in_isochrone,
            "searches_on_node": searches_count,
            "competitor_price": competitor_price,
            "demand_supply_ratio": demand_supply_ratio
        }
