from typing import Optional
from pydantic import BaseModel, Field

class PingRequest(BaseModel):
    driver_id: str = Field(..., description="Уникальный идентификатор водителя/исполнителя")
    lat: float = Field(..., ge=-90.0, le=90.0, description="Широта")
    lon: float = Field(..., ge=-180.0, le=180.0, description="Долгота")
    edge_u: Optional[str] = Field(None, description="Начальный узел ребра")
    edge_v: Optional[str] = Field(None, description="Конечный узел ребра")
    progress: Optional[float] = Field(None, ge=0.0, le=1.0, description="Прогресс движения по ребру")

class SearchRequest(BaseModel):
    search_id: str = Field(..., description="Уникальный идентификатор сессии поиска")
    driver_id: Optional[str] = Field(None, description="Идентификатор водителя для подсчета истории")
    lat: float = Field(..., ge=-90.0, le=90.0, description="Широта поиска")
    lon: float = Field(..., ge=-180.0, le=180.0, description="Долгота поиска")
    dest_lat: Optional[float] = Field(None, ge=-90.0, le=90.0, description="Широта назначения")
    dest_lon: Optional[float] = Field(None, ge=-180.0, le=180.0, description="Долгота назначения")

class CompetitorPriceRequest(BaseModel):
    h3_index: str = Field(..., description="H3 индекс ячейки или ID узла графа")
    price: float = Field(..., ge=0.0, description="Цена конкурента")

class PriceResponse(BaseModel):
    h3_index: str = Field(..., description="H3 ячейка")
    price: float = Field(..., description="Итоговая рассчитанная стоимость")
    base_price: float = Field(..., description="Базовая цена тарифа")
    surge_multiplier: float = Field(..., description="Множитель Surge")
    explanation: str = Field(..., description="Объяснение алгоритма ценообразования")
    is_fail_static: bool = Field(False, description="Признак отката к Fail-Static базовой цене при сбое")
    node_id: Optional[str] = Field(None, description="ID узла графа, если применимо")
    test_group: str = Field("MULTIPLICATIVE", description="Группа Switchback-тестирования (MULTIPLICATIVE / ADDITIVE)")
    surge_bonus: float = Field(0.0, description="Величина аддитивной надбавки")
    payout_formula: str = Field("", description="Математическая формула расчета выплаты")
    causal_uplift_score: Optional[float] = Field(None, description="Оценка uplift ITE от Causal Engine")
    causal_override: bool = Field(False, description="Флаг переопределения надбавки Causal Engine")
    causal_recommended_treatment: Optional[str] = Field(None, description="Рекомендуемый Causal Engine воздействия")


class FaultInjectionRequest(BaseModel):
    enabled: bool = Field(..., description="Флаг активации инжекции сбоя")

