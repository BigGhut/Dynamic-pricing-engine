from typing import Optional

from pydantic import BaseModel, Field

from src.service.schemas import PriceResponse, SearchRequest

__all__ = [
    "CompetitorPriceRequest",
    "FaultInjectionRequest",
    "PingRequest",
    "PriceResponse",
    "SearchRequest",
]

class PingRequest(BaseModel):
    driver_id: str = Field(..., description="Уникальный идентификатор водителя/исполнителя")
    lat: float = Field(..., ge=-90.0, le=90.0, description="Широта")
    lon: float = Field(..., ge=-180.0, le=180.0, description="Долгота")
    edge_u: Optional[str] = Field(None, description="Начальный узел ребра")
    edge_v: Optional[str] = Field(None, description="Конечный узел ребра")
    progress: Optional[float] = Field(None, ge=0.0, le=1.0, description="Прогресс движения по ребру")

class CompetitorPriceRequest(BaseModel):
    h3_index: str = Field(..., description="H3 индекс ячейки или ID узла графа")
    price: float = Field(..., ge=0.0, description="Цена конкурента")

class FaultInjectionRequest(BaseModel):
    enabled: bool = Field(..., description="Флаг активации инжекции сбоя")

