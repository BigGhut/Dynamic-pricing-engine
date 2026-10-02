"""Quote contract. The HTTP layer re-exports these models; it does not own them."""

from typing import Optional

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    search_id: str = Field(..., description="Уникальный идентификатор сессии поиска")
    driver_id: Optional[str] = Field(None, description="Идентификатор водителя для подсчета истории")
    lat: float = Field(..., ge=-90.0, le=90.0, description="Широта поиска")
    lon: float = Field(..., ge=-180.0, le=180.0, description="Долгота поиска")
    dest_lat: Optional[float] = Field(None, ge=-90.0, le=90.0, description="Широта назначения")
    dest_lon: Optional[float] = Field(None, ge=-180.0, le=180.0, description="Долгота назначения")


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
    distance_km: Optional[float] = Field(None, description="Длина поездки, по которой посчитан тариф")
    duration_sec: Optional[float] = Field(None, description="Время поездки, по которому посчитан тариф")
