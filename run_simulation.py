import time
import random
import math
import requests
import sqlite3
from src import config
from src.models.road_graph import RoadGraph

# URL локального FastAPI DPE-сервера
API_URL = "http://localhost:8000"

def save_simulation_analytic(
    test_group: str,
    trip_id: str,
    distance_km: float,
    duration_sec: float,
    price: float,
    surge_bonus: float,
    accepted: int,
    driver_utility: float,
    driver_id: str
):
    """Сохраняет результаты симуляции принятия заказов в SQLite для A/B аналитики."""
    try:
        conn = sqlite3.connect(config.DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO simulation_analytics (
                timestamp, test_group, trip_id, distance_km, duration_sec,
                price, surge_bonus, accepted, driver_utility, driver_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (time.time(), test_group, trip_id, distance_km, duration_sec,
             price, surge_bonus, accepted, driver_utility, driver_id)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Simulator] [ERROR] Ошибка записи аналитики в БД: {e}")

class SimulationRunner:
    def __init__(self, num_drivers: int = 80, num_users: int = 150):
        self.num_drivers = num_drivers
        self.num_users = num_users
        self.graph = RoadGraph()
        
        # Начинаем симуляцию в 07:40 утра для демонстрации утреннего часа пик
        self.virtual_hour = 7.66  # ~07:40
        
        # Инициализируем списки узлов по зонам
        self.center_nodes = ["center"] + [n for n in self.graph.nodes if n.startswith("bulvar_") or n.startswith("sadovoe_")]
        self.suburb_nodes = [n for n in self.graph.nodes if n.startswith("mkad_") or n.startswith("hub_")]
        self.all_nodes = list(self.graph.nodes.keys())

        # Инициализируем водителей по всему графу
        self.drivers = []
        for i in range(self.num_drivers):
            u = random.choice(self.all_nodes)
            while not self.graph.adj[u]:
                u = random.choice(self.all_nodes)
            v = random.choice(list(self.graph.adj[u].keys()))
            
            self.drivers.append({
                "id": f"driver_{i:03d}",
                "edge_u": u,
                "edge_v": v,
                "progress": random.uniform(0.0, 1.0),
                "route": [],
                "lat": self.graph.nodes[u]["lat"],
                "lon": self.graph.nodes[u]["lon"]
            })

    def get_dest_node_probabilistic(self, hour: float) -> str:
        """Выбирает целевой узел на основе маятниковой миграции."""
        is_morning_rush = (6.0 <= hour < 12.0)
        is_evening_rush = (17.0 <= hour < 22.0)
        
        if is_morning_rush:
            if random.random() < 0.85:
                return random.choice(self.suburb_nodes)
            return random.choice(self.center_nodes)
        elif is_evening_rush:
            if random.random() < 0.85:
                return random.choice(self.center_nodes)
            return random.choice(self.suburb_nodes)
        else:
            return random.choice(self.all_nodes)

    def generate_route(self, start: str, dest: str) -> list[str]:
        """Строит маршрут от start до dest с помощью Reverse Dijkstra на графе."""
        if start == dest:
            return []
        
        etas = self.graph.reverse_dijkstra(dest, float("inf"), {})
        
        path = []
        curr = start
        visited = {curr}
        
        while curr != dest:
            neighbors = self.graph.adj[curr]
            best_next = None
            min_eta = float("inf")
            
            for nxt in neighbors:
                if nxt in etas and etas[nxt] < min_eta and nxt not in visited:
                    min_eta = etas[nxt]
                    best_next = nxt
                    
            if best_next is None:
                break
                
            path.append(best_next)
            visited.add(best_next)
            curr = best_next
            
        return path

    def move_drivers(self):
        """Перемещает водителей по ребрам графа и отправляет телеметрию пробок."""
        step_dist_km = 0.5
        hour = self.virtual_hour
        
        for driver in self.drivers:
            u, v = driver["edge_u"], driver["edge_v"]
            edge_data = self.graph.adj[u][v]
            dist = edge_data["distance_km"]
            
            driver["progress"] += step_dist_km / max(dist, 0.1)
            
            if driver["progress"] >= 1.0:
                road_type = edge_data["road_type"]
                base_speed = edge_data["base_speed_kmh"]
                
                factor = 1.0
                if road_type in config.CONGESTION_PROFILES:
                    profiles = config.CONGESTION_PROFILES[road_type]
                    for (h_start, h_end), f in profiles.items():
                        if h_start <= h_end:
                            if h_start <= hour < h_end:
                                factor = f
                                break
                        else:
                            if hour >= h_start or hour < h_end:
                                factor = f
                                break
                                
                simulated_speed = base_speed * factor * random.uniform(0.85, 1.15)
                simulated_speed = max(simulated_speed, 5.0)
                
                try:
                    requests.post(
                        f"{API_URL}/api/v1/telemetry/edge",
                        json={"u": u, "v": v, "speed": simulated_speed},
                        timeout=0.3
                    )
                except Exception:
                    pass
                
                u = v
                driver["edge_u"] = u
                driver["progress"] = 0.0
                
                if not driver["route"]:
                    dest = self.get_dest_node_probabilistic(hour)
                    driver["route"] = self.generate_route(u, dest)
                    if not driver["route"]:
                        driver["route"] = [random.choice(list(self.graph.adj[u].keys()))]
                        
                next_node = driver["route"].pop(0)
                driver["edge_v"] = next_node
                
            u_info = self.graph.nodes[driver["edge_u"]]
            v_info = self.graph.nodes[driver["edge_v"]]
            p = driver["progress"]
            
            driver["lat"] = u_info["lat"] + p * (v_info["lat"] - u_info["lat"])
            driver["lon"] = u_info["lon"] + p * (v_info["lon"] - u_info["lon"])

    def run_tick(self) -> bool:
        """Выполняет один шаг (тик) симуляции."""
        self.virtual_hour = (self.virtual_hour + 15 / 60) % 24
        h = int(self.virtual_hour)
        m = int((self.virtual_hour - h) * 60)
        time_str = f"{h:02d}:{m:02d}"
        
        print(f"\n[Simulator] === Время суток: {time_str} ===")

        try:
            requests.post(f"{API_URL}/api/v1/virtual_hour", json={"hour": self.virtual_hour}, timeout=0.5)
        except Exception:
            pass

        self.move_drivers()
        print(f"[Simulator] Отправка координат для {len(self.drivers)} водителей...")
        for driver in self.drivers:
            try:
                requests.post(
                    f"{API_URL}/api/v1/ping",
                    json={
                        "driver_id": driver["id"],
                        "lat": driver["lat"],
                        "lon": driver["lon"],
                        "edge_u": driver["edge_u"],
                        "edge_v": driver["edge_v"],
                        "progress": driver["progress"]
                    },
                    timeout=0.8
                )
            except requests.exceptions.ConnectionError:
                print(f"[Simulator] [ERROR] Нет подключения к серверу DPE по адресу {API_URL}.")
                return False
            except Exception:
                pass

        # Генерируем цену конкурента
        try:
            ref_driver = random.choice(self.drivers)
            node_id = ref_driver["edge_v"]
            comp_price = round(config.BASE_PRICE * random.uniform(0.85, 1.45), 1)
            requests.post(
                f"{API_URL}/api/v1/competitor",
                json={"h3_index": node_id, "price": comp_price},
                timeout=0.5
            )
        except Exception:
            pass

        # Генерируем поисковые запросы (с дестинациями)
        hour = self.virtual_hour
        if 6.0 <= hour < 12.0:
            phase = "Утренний час пик (спрос на окраинах)"
            num_searches = random.randint(18, 30)
            location_types = ["suburbs", "center"]
            weights = [0.85, 0.15]
        elif 12.0 <= hour < 17.0:
            phase = "Дневное затишье"
            num_searches = random.randint(8, 15)
            location_types = ["any"]
            weights = [1.0]
        elif 17.0 <= hour < 22.0:
            phase = "Вечерний час пик (спрос в центре)"
            num_searches = random.randint(18, 30)
            location_types = ["center", "suburbs"]
            weights = [0.85, 0.15]
        else:
            phase = "Ночное время"
            num_searches = random.randint(2, 6)
            location_types = ["any"]
            weights = [1.0]

        print(f"[Simulator] Фаза: {phase} | Симулируем {num_searches} запросов пользователей...")
        
        for _ in range(num_searches):
            loc_type = random.choices(location_types, weights=weights)[0]
            if loc_type == "suburbs":
                target_node = random.choice(self.suburb_nodes)
            elif loc_type == "center":
                target_node = random.choice(self.center_nodes)
            else:
                target_node = random.choice(self.all_nodes)
                
            node_info = self.graph.nodes[target_node]
            lat = node_info["lat"] + random.uniform(-0.005, 0.005)
            lon = node_info["lon"] + random.uniform(-0.008, 0.008)
            
            # Добавим случайный пункт назначения
            dest_node = random.choice(self.all_nodes)
            while dest_node == target_node:
                dest_node = random.choice(self.all_nodes)
            dest_info = self.graph.nodes[dest_node]
            dest_lat = dest_info["lat"] + random.uniform(-0.005, 0.005)
            dest_lon = dest_info["lon"] + random.uniform(-0.008, 0.008)
            
            search_id = f"search_{random.randint(100000, 999999)}"
            try:
                resp = requests.post(
                    f"{API_URL}/api/v1/search",
                    json={
                        "search_id": search_id,
                        "lat": lat,
                        "lon": lon,
                        "dest_lat": dest_lat,
                        "dest_lon": dest_lon
                    },
                    timeout=0.8
                )
                if resp.status_code == 200:
                    data = resp.json()
                    
                    price = data.get("price", config.BASE_PRICE)
                    base_price = data.get("base_price", config.BASE_PRICE)
                    surge_mult = data.get("surge_multiplier", 1.0)
                    surge_bonus = data.get("surge_bonus", 0.0)
                    test_group = data.get("test_group", "MULTIPLICATIVE")
                    payout_formula = data.get("payout_formula", "")
                    
                    # Симулируем расчет параметров поездки для полезности водителя
                    d_time, d_dist = self.graph.shortest_path_od(target_node, dest_node, {})
                    if d_time == float("inf") or d_time <= 0.0:
                        trip_time_sec = 900.0
                        trip_dist_km = 7.0
                    else:
                        trip_time_sec = d_time
                        trip_dist_km = d_dist
                    
                    # Расчет полезности водителя (руб/час)
                    commission = 0.2
                    cost_per_km = 6.0
                    pickup_time_sec = 240.0 # 4 минуты подача
                    
                    driver_revenue = price * (1.0 - commission)
                    driver_cost = trip_dist_km * cost_per_km
                    total_time_hours = (trip_time_sec + pickup_time_sec) / 3600.0
                    
                    utility_per_hour = (driver_revenue - driver_cost) / max(total_time_hours, 0.05)
                    
                    # Эмуляция Cherry-picking для мультипликативного сурджа
                    if test_group == "MULTIPLICATIVE" and trip_dist_km < 5.0:
                        if surge_mult > 1.1:
                            # Водитель занижает полезность короткой поездки в зоне высокого сурджа, надеясь дождаться длинной
                            cherry_penalty = max(0.15, 1.0 - 0.85 * (surge_mult - 1.0))
                            utility_per_hour *= cherry_penalty
                    
                    # Логистическая функция принятия заказа
                    k = 0.004
                    U_threshold = 900.0
                    p_accept = 1.0 / (1.0 + math.exp(-k * (utility_per_hour - U_threshold)))
                    
                    accepted = 1 if random.random() < p_accept else 0
                    
                    # Сохраняем в аналитику
                    random_driver = random.choice(self.drivers)["id"]
                    save_simulation_analytic(
                        test_group=test_group,
                        trip_id=search_id,
                        distance_km=trip_dist_km,
                        duration_sec=trip_time_sec,
                        price=price,
                        surge_bonus=surge_bonus,
                        accepted=accepted,
                        driver_utility=utility_per_hour,
                        driver_id=random_driver
                    )
                    
                    if random.random() < 0.15:
                        accept_status = "Принят" if accepted else "Отклонен"
                        print(
                            f"   -> Поиск {search_id} ({test_group}) | {target_node} -> {dest_node} | "
                            f"Цена: {price} руб (Бонус: {surge_bonus} руб) | "
                            f"Утилизация: {round(utility_per_hour)} руб/ч | Статус: {accept_status}"
                        )
            except Exception as e:
                print(f"[Simulator] [ERROR] Ошибка симуляции запроса: {e}")
                pass

        return True

def main():
    print("=== DPE MVP-4.0 Симулятор Трафика с Additive Surge ===")
    print(f"Подключение к DPE серверу: {API_URL}")
    
    try:
        requests.get(f"{API_URL}/health", timeout=1.5)
        print("[Simulator] Успешное подключение к серверу ценообразования.")
    except Exception:
        print("[Simulator] [WARN] DPE сервер не доступен.")
        
    sim = SimulationRunner()
    
    try:
        while True:
            success = sim.run_tick()
            if not success:
                print("[Simulator] Сервер ценообразования не отвечает... (повтор через 5 сек)")
                time.sleep(5)
                continue
            time.sleep(4)
    except KeyboardInterrupt:
        print("\n[Simulator] Симуляция остановлена.")

if __name__ == "__main__":
    main()
