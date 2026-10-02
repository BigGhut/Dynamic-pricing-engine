"""Simulator-only routes. They answer only when SIM_MODE is on, and they stay out of the public schema."""

from fastapi import APIRouter, HTTPException, Request, status

from src import config
from src.api.schemas import FaultInjectionRequest

router = APIRouter(include_in_schema=False)


def _require_sim() -> None:
    if not config.SIM_MODE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


@router.post("/api/v1/inject_fault", status_code=status.HTTP_200_OK)
def inject_fault(payload: FaultInjectionRequest):
    """Включает или выключает симуляцию сбоя в ML-микросервисе."""
    _require_sim()
    config.FAULT_INJECTION_ACTIVE = payload.enabled
    status_str = "activated" if payload.enabled else "deactivated"
    print(f"[API] [FAULT_INJECTION] Fault injection {status_str}")
    return {"status": "success", "fault_injection_active": config.FAULT_INJECTION_ACTIVE}


@router.post("/api/v1/telemetry/edge", status_code=status.HTTP_200_OK)
def register_edge_telemetry(payload: dict, request: Request):
    """Регистрирует телеметрию скорости на конкретном ребре графа."""
    _require_sim()
    u = payload.get("u")
    v = payload.get("v")
    speed = payload.get("speed")
    if u and v and speed is not None:
        request.app.state.feature_store.register_edge_telemetry(u, v, speed)
        return {"status": "success"}
    return {"status": "error", "message": "Invalid telemetry payload"}


@router.post("/api/v1/virtual_hour", status_code=status.HTTP_200_OK)
def set_virtual_hour(payload: dict, request: Request):
    """Устанавливает текущее время симуляции для расчета заторов."""
    _require_sim()
    hour = payload.get("hour", 12.0)
    request.app.state.feature_store.set_sim_virtual_hour(hour)
    return {"status": "success", "virtual_hour": hour}
