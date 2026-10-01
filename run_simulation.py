import os
import random
import sqlite3
import time

import requests

from src import clock, config
from src.models.road_graph import RoadGraph
from src.pricing import accept_probability
from src.runtime import HttpEngine

API_URL = os.getenv("API_URL", "http://localhost:8000")


def save_simulation_analytic(
    test_group: str,
    trip_id: str,
    distance_km: float,
    duration_sec: float,
    price: float,
    surge_bonus: float,
    accepted: int,
    driver_utility: float,
    driver_id: str,
):
    """Сохраняет результаты симуляции принятия заказов в SQLite для A/B аналитики."""
    try:
        conn = sqlite3.connect(config.DB_PATH, timeout=5)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO simulation_analytics (
                timestamp, test_group, trip_id, distance_km, duration_sec,
                price, surge_bonus, accepted, driver_utility, driver_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                clock.now(),
                test_group,
                trip_id,
                distance_km,
                duration_sec,
                price,
                surge_bonus,
                accepted,
                driver_utility,
                driver_id,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as error:
        print(f"[Simulator] [ERROR] Ошибка записи аналитики в БД: {error}")


class SimulationRunner:
    def __init__(self, num_drivers: int = 80, num_users: int = 150, engine=None, verbose: bool = True):
        self.num_drivers = num_drivers
        self.num_users = num_users
        self.engine = engine or HttpEngine(API_URL)
        self.verbose = verbose
        self.records: list[dict] = []
        self.graph = RoadGraph()

        # 07:40, утренний час пик. Каждый тик прибавляет 15 минут.
        self.virtual_hour = 7.66

        self.center_nodes = ["center"] + [
            node for node in self.graph.nodes if node.startswith("bulvar_") or node.startswith("sadovoe_")
        ]
        self.suburb_nodes = [
            node for node in self.graph.nodes if node.startswith("mkad_") or node.startswith("hub_")
        ]
        self.all_nodes = list(self.graph.nodes.keys())

        self.drivers = []
        for i in range(self.num_drivers):
            origin = random.choice(self.all_nodes)
            while not self.graph.adj[origin]:
                origin = random.choice(self.all_nodes)
            destination = random.choice(list(self.graph.adj[origin].keys()))
            self.drivers.append({
                "id": f"driver_{i:03d}",
                "edge_u": origin,
                "edge_v": destination,
                "progress": random.uniform(0.0, 1.0),
                "route": [],
                "lat": self.graph.nodes[origin]["lat"],
                "lon": self.graph.nodes[origin]["lon"],
            })

    def get_dest_node_probabilistic(self, hour: float) -> str:
        """Утро: в центр. Вечер: на окраину. Иначе равномерно по графу."""
        is_morning_rush = 6.0 <= hour < 12.0
        is_evening_rush = 17.0 <= hour < 22.0

        if is_morning_rush:
            if random.random() < 0.85:
                return random.choice(self.center_nodes)
            return random.choice(self.suburb_nodes)
        if is_evening_rush:
            if random.random() < 0.85:
                return random.choice(self.suburb_nodes)
            return random.choice(self.center_nodes)
        return random.choice(self.all_nodes)

    def generate_route(self, start: str, dest: str) -> list[str]:
        """Строит маршрут от start до dest с помощью Reverse Dijkstra на графе."""
        if start == dest:
            return []

        etas = self.graph.reverse_dijkstra(dest, float("inf"), {})
        path = []
        current = start
        visited = {current}

        while current != dest:
            best_next = None
            min_eta = float("inf")
            for nxt in self.graph.adj[current]:
                if nxt in etas and etas[nxt] < min_eta and nxt not in visited:
                    min_eta = etas[nxt]
                    best_next = nxt
            if best_next is None:
                break
            path.append(best_next)
            visited.add(best_next)
            current = best_next
        return path

    def move_drivers(self):
        """Перемещает водителей по ребрам графа и отправляет телеметрию пробок."""
        step_dist_km = 0.5
        hour = self.virtual_hour

        for driver in self.drivers:
            origin, destination = driver["edge_u"], driver["edge_v"]
            edge_data = self.graph.adj[origin][destination]
            distance = edge_data["distance_km"]
            driver["progress"] += step_dist_km / max(distance, 0.1)

            if driver["progress"] >= 1.0:
                road_type = edge_data["road_type"]
                base_speed = edge_data["base_speed_kmh"]
                factor = 1.0
                if road_type in config.CONGESTION_PROFILES:
                    for (start_hour, end_hour), profile_factor in config.CONGESTION_PROFILES[road_type].items():
                        if start_hour <= end_hour:
                            if start_hour <= hour < end_hour:
                                factor = profile_factor
                                break
                        elif hour >= start_hour or hour < end_hour:
                            factor = profile_factor
                            break

                simulated_speed = max(base_speed * factor * random.uniform(0.85, 1.15), 5.0)
                self.engine.telemetry(origin, destination, simulated_speed)

                driver["edge_u"] = destination
                driver["progress"] = 0.0
                if not driver["route"]:
                    route_dest = self.get_dest_node_probabilistic(hour)
                    driver["route"] = self.generate_route(destination, route_dest)
                    if not driver["route"]:
                        driver["route"] = [random.choice(list(self.graph.adj[destination].keys()))]
                driver["edge_v"] = driver["route"].pop(0)

            origin_info = self.graph.nodes[driver["edge_u"]]
            dest_info = self.graph.nodes[driver["edge_v"]]
            progress = driver["progress"]
            driver["lat"] = origin_info["lat"] + progress * (dest_info["lat"] - origin_info["lat"])
            driver["lon"] = origin_info["lon"] + progress * (dest_info["lon"] - origin_info["lon"])

    def _log(self, message: str):
        if self.verbose:
            print(message)

    def run_tick(self) -> bool:
        """Выполняет один шаг (тик) симуляции."""
        self.virtual_hour = (self.virtual_hour + 15 / 60) % 24
        hour_index = int(self.virtual_hour)
        minute = int((self.virtual_hour - hour_index) * 60)
        self._log(f"\n[Simulator] === Время суток: {hour_index:02d}:{minute:02d} ===")
        self.engine.set_hour(self.virtual_hour)

        self.move_drivers()
        self._log(f"[Simulator] Отправка координат для {len(self.drivers)} водителей...")
        for driver in self.drivers:
            if self.engine.ping(driver) == "down":
                print(f"[Simulator] [ERROR] Нет подключения к серверу DPE по адресу {API_URL}.")
                return False

        try:
            reference = random.choice(self.drivers)
            competitor_price = round(config.BASE_PRICE * random.uniform(0.85, 1.45), 1)
            self.engine.competitor(reference["edge_v"], competitor_price)
        except Exception:
            pass

        hour = self.virtual_hour
        if 6.0 <= hour < 12.0:
            phase = "Утренний час пик (спрос на окраинах, назначение в центре)"
            num_searches = random.randint(18, 30)
            location_types = ["suburbs", "center"]
            weights = [0.85, 0.15]
        elif 12.0 <= hour < 17.0:
            phase = "Дневное затишье"
            num_searches = random.randint(8, 15)
            location_types = ["any"]
            weights = [1.0]
        elif 17.0 <= hour < 22.0:
            phase = "Вечерний час пик (спрос в центре, назначение на окраине)"
            num_searches = random.randint(18, 30)
            location_types = ["center", "suburbs"]
            weights = [0.85, 0.15]
        else:
            phase = "Ночное время"
            num_searches = random.randint(2, 6)
            location_types = ["any"]
            weights = [1.0]

        self._log(f"[Simulator] Фаза: {phase} | Симулируем {num_searches} запросов пользователей...")

        for _ in range(num_searches):
            location_type = random.choices(location_types, weights=weights)[0]
            if location_type == "suburbs":
                target_node = random.choice(self.suburb_nodes)
            elif location_type == "center":
                target_node = random.choice(self.center_nodes)
            else:
                target_node = random.choice(self.all_nodes)

            node_info = self.graph.nodes[target_node]
            lat = node_info["lat"] + random.uniform(-0.005, 0.005)
            lon = node_info["lon"] + random.uniform(-0.008, 0.008)

            dest_node = random.choice(self.all_nodes)
            while dest_node == target_node:
                dest_node = random.choice(self.all_nodes)
            dest_info = self.graph.nodes[dest_node]
            search_id = f"search_{random.randint(100000, 999999)}"
            data = self.engine.search({
                "search_id": search_id,
                "lat": lat,
                "lon": lon,
                "dest_lat": dest_info["lat"] + random.uniform(-0.005, 0.005),
                "dest_lon": dest_info["lon"] + random.uniform(-0.008, 0.008),
            })
            if not data:
                continue

            price = data.get("price", config.BASE_PRICE)
            surge_bonus = data.get("surge_bonus", 0.0)
            test_group = data.get("test_group", "MULTIPLICATIVE")
            surge_multiplier = data.get("surge_multiplier", 1.0)
            trip_dist_km = data.get("distance_km")
            trip_time_sec = data.get("duration_sec")
            if not trip_dist_km or not trip_time_sec:
                d_time, d_dist = self.graph.shortest_path_od(target_node, dest_node, {})
                if d_time == float("inf") or d_time <= 0.0:
                    trip_time_sec = 900.0
                    trip_dist_km = 7.0
                else:
                    trip_time_sec = d_time
                    trip_dist_km = d_dist

            probability = accept_probability(
                price=price,
                distance_km=trip_dist_km,
                duration_sec=trip_time_sec,
                arm=test_group,
                surge_multiplier=surge_multiplier,
            )
            accepted = 1 if random.random() < probability else 0
            driver_id = random.choice(self.drivers)["id"]
            self.records.append({
                "test_group": test_group,
                "trip_id": search_id,
                "distance_km": trip_dist_km,
                "duration_sec": trip_time_sec,
                "price": price,
                "surge_bonus": surge_bonus,
                "accepted": accepted,
                "driver_utility": probability,
                "driver_id": driver_id,
            })
            save_simulation_analytic(
                test_group=test_group,
                trip_id=search_id,
                distance_km=trip_dist_km,
                duration_sec=trip_time_sec,
                price=price,
                surge_bonus=surge_bonus,
                accepted=accepted,
                driver_utility=probability,
                driver_id=driver_id,
            )
            if self.verbose and random.random() < 0.15:
                status = "Принят" if accepted else "Отклонен"
                print(
                    f"   -> Поиск {search_id} ({test_group}) | {target_node} -> {dest_node} | "
                    f"Цена: {price} руб (Бонус: {surge_bonus} руб) | "
                    f"P(accept): {round(probability, 2)} | Статус: {status}"
                )
        return True


def main():
    print("=== DPE Симулятор трафика: additive surge против multiplicative ===")
    print(f"Подключение к DPE серверу: {API_URL}")
    try:
        requests.get(f"{API_URL}/health", timeout=1.5)
        print("[Simulator] Успешное подключение к серверу ценообразования.")
    except Exception:
        print("[Simulator] [WARN] DPE сервер не доступен.")

    simulator = SimulationRunner()
    try:
        while True:
            if not simulator.run_tick():
                print("[Simulator] Сервер ценообразования не отвечает... (повтор через 5 сек)")
                time.sleep(5)
                continue
            time.sleep(4)
    except KeyboardInterrupt:
        print("\n[Simulator] Симуляция остановлена.")


if __name__ == "__main__":
    main()
