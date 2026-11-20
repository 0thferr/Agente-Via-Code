from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUserDep, SessionDep
from app.schemas.dashboard import (
    DashboardFilters,
    FunnelOut,
    OverviewOut,
    PlatformMetrics,
    TimelineFilters,
    TimelineOut,
)
from app.services import dashboard_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
Filters = Annotated[DashboardFilters, Query()]


@router.get("/overview", response_model=OverviewOut)
def overview(filters: Filters, session: SessionDep, current_user: CurrentUserDep) -> OverviewOut:
    """Totais do período: vagas, análises, candidaturas, etapas e taxas de conversão."""
    return dashboard_service.overview(session, current_user.id, filters)


@router.get("/funnel", response_model=FunnelOut)
def funnel(filters: Filters, session: SessionDep, current_user: CurrentUserDep) -> FunnelOut:
    """Funil da vaga encontrada até a contratação, com a taxa entre etapas."""
    return dashboard_service.funnel(session, current_user.id, filters)


@router.get("/platforms", response_model=list[PlatformMetrics])
def platforms(
    filters: Filters, session: SessionDep, current_user: CurrentUserDep
) -> list[PlatformMetrics]:
    """Métricas por plataforma de origem das vagas."""
    return dashboard_service.platforms(session, current_user.id, filters)


@router.get("/timeline", response_model=TimelineOut)
def timeline(
    filters: Annotated[TimelineFilters, Query()],
    session: SessionDep,
    current_user: CurrentUserDep,
) -> TimelineOut:
    """Séries por dia/semana/mês: vagas, envios, entrevistas e execuções do agente."""
    return dashboard_service.timeline(session, current_user.id, filters)
