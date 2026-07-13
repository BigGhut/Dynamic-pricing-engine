import math
import heapq
import h3
from src import config

class RoadGraph:
    def __init__(self):
        # node_id -> {neighbor_id: {distance_km: float, base_speed_kmh: float, road_type: str}}
        self.adj = {}
        # predecessor_id -> {node_id: {distance_km: float, base_speed_kmh: float, road_type: str}}
        self.adj_transposed = {}
        # node_id -> {"lat": float, "lon": float, "name": str, "h3_cell": str}
        self.nodes = {}
        
        self._build_moscow_graph()

    def add_node(self, node_id: str, lat: float, lon: float, name: str):
        h3_cell = h3.latlng_to_cell(lat, lon, config.H3_RESOLUTION)
        self.nodes[node_id] = {
            "lat": lat,
            "lon": lon,
            "name": name,
            "h3_cell": h3_cell
        }
        if node_id not in self.adj:
            self.adj[node_id] = {}
        if node_id not in self.adj_transposed:
            self.adj_transposed[node_id] = {}

    def add_edge(self, u: str, v: str, dist_km: float, base_speed_kmh: float, road_type: str, bidirectional: bool = True):
        # Прямое ребро
        self.adj[u][v] = {
            "distance_km": dist_km,
            "base_speed_kmh": base_speed_kmh,
            "road_type": road_type
        }
        self.adj_transposed[v][u] = {
            "distance_km": dist_km,
            "base_speed_kmh": base_speed_kmh,
            "road_type": road_type
        }
        
        # Обратное ребро
        if bidirectional:
            self.adj[v][u] = {
                "distance_km": dist_km,
                "base_speed_kmh": base_speed_kmh,
                "road_type": road_type
            }
            self.adj_transposed[u][v] = {
                "distance_km": dist_km,
                "base_speed_kmh": base_speed_kmh,
                "road_type": road_type
            }

    def _build_moscow_graph(self):
        """
        Строит реалистичный радиально-кольцевой дорожный граф Москвы.
        Содержит 70 узлов:
        - Центр: 1 узел (Кремль)
        - Бульварное кольцо (8 радиальных направлений на расстоянии 1.2 км) -> 8 узлов
        - Садовое кольцо (8 направлений на расстоянии 2.5 км) -> 8 узлов
        - Третье кольцо (ТТК) (12 направлений на расстоянии 5.0 км) -> 12 узлов
        - Магистральные хабы (16 направлений на расстоянии 10.0 км) -> 16 узлов
        - МКАД и окраины (25 направлений на расстоянии 17.5 км) -> 25 узлов
        """
        center_lat = config.SIM_TOWN_CENTER_LAT
        center_lon = config.SIM_TOWN_CENTER_LON
        
        # 1. Добавляем центр
        self.add_node("center", center_lat, center_lon, "Кремль")
        
        # Вспомогательная функция для генерации координат по углу и радиусу
        def get_coords(radius_km: float, angle_deg: float) -> tuple[float, float]:
            # 1 градус широты ~ 111.1 км
            # 1 градус долготы ~ 111.1 * cos(lat) км
            lat = center_lat + (radius_km / 111.1) * math.sin(math.radians(angle_deg))
            lon = center_lon + (radius_km / (111.1 * math.cos(math.radians(center_lat)))) * math.cos(math.radians(angle_deg))
            return lat, lon

        # 2. Бульварное кольцо (радиус 1.2 км, 8 узлов)
        bulvar_nodes = []
        for i in range(8):
            angle = i * 45
            lat, lon = get_coords(1.2, angle)
            node_id = f"bulvar_{i}"
            name = f"Бульварное кольцо {angle}°"
            self.add_node(node_id, lat, lon, name)
            bulvar_nodes.append(node_id)
            # Связываем с центром (радиальные улицы)
            # Расстояние ~ 1.2 км, скорость 40 км/ч, тип - внутриквартальные
            self.add_edge("center", node_id, 1.2, 40.0, "inside", bidirectional=True)
            
        # Связываем Бульварное по кругу
        for i in range(8):
            u = bulvar_nodes[i]
            v = bulvar_nodes[(i + 1) % 8]
            # Длина дуги L = r * theta (в радианах) -> 1.2 * (45 * pi / 180) ~ 0.94 км
            self.add_edge(u, v, 0.94, 50.0, "inside", bidirectional=True)

        # 3. Садовое кольцо (радиус 2.5 км, 8 узлов)
        sadovoe_nodes = []
        for i in range(8):
            angle = i * 45
            lat, lon = get_coords(2.5, angle)
            node_id = f"sadovoe_{i}"
            name = f"Садовое кольцо {angle}°"
            self.add_node(node_id, lat, lon, name)
            sadovoe_nodes.append(node_id)
            # Связываем радиально с Бульварным кольцом
            self.add_edge(f"bulvar_{i}", node_id, 1.3, 50.0, "inside", bidirectional=True)

        # Связываем Садовое по кругу
        for i in range(8):
            u = sadovoe_nodes[i]
            v = sadovoe_nodes[(i + 1) % 8]
            # L = 2.5 * 0.785 ~ 1.96 км
            self.add_edge(u, v, 1.96, 60.0, "sadovoe", bidirectional=True)

        # 4. Третье Транспортное Кольцо (ТТК) (радиус 5.0 км, 12 узлов)
        ttk_nodes = []
        for i in range(12):
            angle = i * 30
            lat, lon = get_coords(5.0, angle)
            node_id = f"ttk_{i}"
            name = f"ТТК {angle}°"
            self.add_node(node_id, lat, lon, name)
            ttk_nodes.append(node_id)
            # Связываем радиально с Садовым кольцом (ближайшим по углу)
            sadovoe_idx = round(angle / 45) % 8
            self.add_edge(f"sadovoe_{sadovoe_idx}", node_id, 2.5, 60.0, "radial", bidirectional=True)

        # Связываем ТТК по кругу
        for i in range(12):
            u = ttk_nodes[i]
            v = ttk_nodes[(i + 1) % 12]
            # L = 5.0 * (30 * pi / 180) ~ 2.62 км
            self.add_edge(u, v, 2.62, 80.0, "ttk", bidirectional=True)

        # 5. Магистральные хабы (радиус 10.0 км, 16 узлов)
        hub_nodes = []
        for i in range(16):
            angle = i * 22.5
            lat, lon = get_coords(10.0, angle)
            node_id = f"hub_{i}"
            name = f"Радиальный Хаб {angle}°"
            self.add_node(node_id, lat, lon, name)
            hub_nodes.append(node_id)
            # Связываем радиально с ближайшим узлом ТТК
            ttk_idx = round(angle / 30) % 12
            self.add_edge(f"ttk_{ttk_idx}", node_id, 5.0, 70.0, "radial", bidirectional=True)

        # 6. МКАД и Окраины (радиус 17.5 км, 25 узлов)
        mkad_nodes = []
        for i in range(25):
            angle = i * 14.4
            lat, lon = get_coords(17.5, angle)
            node_id = f"mkad_{i}"
            name = f"МКАД {angle}°"
            self.add_node(node_id, lat, lon, name)
            mkad_nodes.append(node_id)
            # Связываем радиально с ближайшим хабом
            hub_idx = round(angle / 22.5) % 16
            self.add_edge(f"hub_{hub_idx}", node_id, 7.5, 80.0, "radial", bidirectional=True)

        # Связываем МКАД по кругу
        for i in range(25):
            u = mkad_nodes[i]
            v = mkad_nodes[(i + 1) % 25]
            # L = 17.5 * (14.4 * pi / 180) ~ 4.4 км
            self.add_edge(u, v, 4.4, 100.0, "mkad", bidirectional=True)

        # Симулируем водную преграду (Москва-реку):
        # Разорвем радиальные ребра через реку в зонах, где нет мостов
        if "ttk_4" in self.adj and "hub_6" in self.adj["ttk_4"]:
            del self.adj["ttk_4"]["hub_6"]
            del self.adj["hub_6"]["ttk_4"]
            del self.adj_transposed["ttk_4"]["hub_6"]
            del self.adj_transposed["hub_6"]["ttk_4"]
            
        if "ttk_10" in self.adj and "hub_14" in self.adj["ttk_10"]:
            del self.adj["ttk_10"]["hub_14"]
            del self.adj["hub_14"]["ttk_10"]
            del self.adj_transposed["ttk_10"]["hub_14"]
            del self.adj_transposed["hub_14"]["ttk_10"]

    def snap_to_node(self, lat: float, lon: float) -> str:
        """
        Привязывает GPS-координаты к ближайшему узлу графа (Haversine линейный поиск).
        """
        best_node = "center"
        min_dist = float("inf")
        
        # Haversine
        for node_id, node_info in self.nodes.items():
            n_lat = node_info["lat"]
            n_lon = node_info["lon"]
            
            # Быстрая аппроксимация расстояния в км для скорости
            d_lat = n_lat - lat
            d_lon = (n_lon - lon) * math.cos(math.radians(lat))
            dist = 111.1 * math.sqrt(d_lat*d_lat + d_lon*d_lon)
            
            if dist < min_dist:
                min_dist = dist
                best_node = node_id
                
        return best_node

    def reverse_dijkstra(self, target_node: str, max_eta_sec: float, edge_speeds: dict[tuple[str, str], float]) -> dict[str, float]:
        """
        Вычисляет ETA от всех узлов графа ДО target_node на транспонированном графе.
        Возвращает словарь {node_id: eta_sec} для узлов, где eta_sec <= max_eta_sec.
        """
        if target_node not in self.nodes:
            return {}

        # Инициализируем расстояния
        etas = {node_id: float("inf") for node_id in self.nodes}
        etas[target_node] = 0.0
        
        # Очередь с приоритетами: (eta_sec, node_id)
        pq = [(0.0, target_node)]
        
        while pq:
            current_eta, u = heapq.heappop(pq)
            
            if current_eta > etas[u]:
                continue
                
            if current_eta > max_eta_sec:
                break
                
            # Проходим по входящим ребрам в оригинальном графе (то есть исходящим в транспонированном)
            for predecessor, edge_data in self.adj_transposed[u].items():
                dist = edge_data["distance_km"]
                
                # Получаем динамическую скорость
                speed = edge_speeds.get((predecessor, u), edge_data["base_speed_kmh"])
                
                # Время в секундах
                travel_time = (dist / max(speed, 5.0)) * 3600.0
                
                new_eta = current_eta + travel_time
                if new_eta < etas[predecessor]:
                    etas[predecessor] = new_eta
                    heapq.heappush(pq, (new_eta, predecessor))
                    
        # Фильтруем по порогу
        return {node_id: eta for node_id, eta in etas.items() if eta <= max_eta_sec}

    def shortest_path_od(self, origin: str, dest: str, edge_speeds: dict[tuple[str, str], float]) -> tuple[float, float]:
        """
        Вычисляет кратчайший путь от origin до dest на графе с учётом динамических скоростей.
        Возвращает кортеж (travel_time_sec, distance_km).
        Если путь не найден, возвращает (inf, inf).
        """
        if origin not in self.nodes or dest not in self.nodes:
            return float("inf"), float("inf")
            
        if origin == dest:
            return 0.0, 0.0
            
        # Инициализируем расстояния: node_id -> (time_sec, dist_km)
        best = {node_id: (float("inf"), float("inf")) for node_id in self.nodes}
        best[origin] = (0.0, 0.0)
        
        pq = [(0.0, 0.0, origin)]  # (time_sec, dist_km, node_id)
        
        while pq:
            current_time, current_dist, u = heapq.heappop(pq)
            
            if current_time > best[u][0]:
                continue
                
            if u == dest:
                return current_time, current_dist
                
            for v, edge_data in self.adj[u].items():
                dist = edge_data["distance_km"]
                speed = edge_speeds.get((u, v), edge_data["base_speed_kmh"])
                travel_time = (dist / max(speed, 5.0)) * 3600.0
                
                new_time = current_time + travel_time
                new_dist = current_dist + dist
                
                if new_time < best[v][0]:
                    best[v] = (new_time, new_dist)
                    heapq.heappush(pq, (new_time, new_dist, v))
                    
        return best[dest]
