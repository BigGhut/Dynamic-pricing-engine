from typing import List, Dict
import h3
from src import config

def get_h3_index(lat: float, lon: float, resolution: int = config.H3_RESOLUTION) -> str:
    """Возвращает H3 индекс для заданных географических координат."""
    return h3.latlng_to_cell(lat, lon, resolution)

def get_k_ring(h3_index: str, ring_size: int = config.K_RING_RADIUS) -> List[str]:
    """Возвращает список соседних H3 индексов в радиусе ring_size."""
    return list(h3.grid_disk(h3_index, ring_size))

def smooth_metric_k_ring(
    h3_index: str, 
    metrics_map: Dict[str, float], 
    ring_size: int = config.K_RING_RADIUS
) -> float:
    """
    Выполняет пространственное сглаживание метрики (например, цены)
    для ячейки h3_index с учетом ее соседей в радиусе k-Ring.
    Использует веса: 1.0 для центральной ячейки, 0.15 для соседей первого кольца (dist = 1),
    и отключает влияние второго кольца и далее (dist >= 2).
    """
    neighbors = get_k_ring(h3_index, ring_size)
    
    total_weight = 0.0
    weighted_sum = 0.0
    
    for neighbor in neighbors:
        val = metrics_map.get(neighbor, 0.0)
        
        try:
            # Находим точное расстояние по сетке гексагонов
            dist = h3.grid_distance(h3_index, neighbor)
        except Exception:
            # Откат в случае некорректных ячеек
            dist = 0 if neighbor == h3_index else 1
            
        if dist == 0:
            weight = 1.0
        elif dist == 1:
            weight = 0.15
        else:
            weight = 0.0  # Полностью отключаем второе кольцо и далее
            
        weighted_sum += val * weight
        total_weight += weight
        
    if total_weight == 0.0:
        return 0.0
        
    return weighted_sum / total_weight
