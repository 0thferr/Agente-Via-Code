import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUserDep, SessionDep
from app.schemas.automation import (
    AutomationRuleCreate,
    AutomationRuleOut,
    AutomationRuleUpdate,
    ExecutionFilters,
    ExecutionListResponse,
    ScheduledJobOut,
)
from app.services import automation_service

router = APIRouter(prefix="/automations", tags=["automations"])


@router.get("", response_model=list[AutomationRuleOut])
def list_rules(session: SessionDep, current_user: CurrentUserDep) -> list[AutomationRuleOut]:
    rules = automation_service.list_rules(session, current_user.id)
    return [AutomationRuleOut.model_validate(rule) for rule in rules]


@router.post("", response_model=AutomationRuleOut, status_code=status.HTTP_201_CREATED)
def create_rule(
    payload: AutomationRuleCreate, session: SessionDep, current_user: CurrentUserDep
) -> AutomationRuleOut:
    """Cria uma rotina (ex.: buscar vagas de segunda a sexta às 08:00 em America/Sao_Paulo)."""
    rule = automation_service.create_rule(session, current_user.id, payload)
    return AutomationRuleOut.model_validate(rule)


@router.get("/executions", response_model=ExecutionListResponse)
def list_executions(
    filters: Annotated[ExecutionFilters, Query()],
    session: SessionDep,
    current_user: CurrentUserDep,
) -> ExecutionListResponse:
    """Histórico de execuções do agente, com o log de cada tentativa."""
    jobs, total = automation_service.list_executions(session, current_user.id, filters)
    return ExecutionListResponse(
        items=[ScheduledJobOut.model_validate(job) for job in jobs],
        total=total,
        limit=filters.limit,
        offset=filters.offset,
    )


@router.patch("/{rule_id}", response_model=AutomationRuleOut)
def update_rule(
    rule_id: uuid.UUID,
    payload: AutomationRuleUpdate,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> AutomationRuleOut:
    rule = automation_service.update_rule(session, current_user.id, rule_id, payload)
    return AutomationRuleOut.model_validate(rule)


@router.post("/{rule_id}/run", response_model=ScheduledJobOut, status_code=status.HTTP_202_ACCEPTED)
def run_rule(
    rule_id: uuid.UUID, session: SessionDep, current_user: CurrentUserDep
) -> ScheduledJobOut:
    """Enfileira uma execução imediata; o worker a processa no próximo ciclo."""
    job = automation_service.run_now(session, current_user.id, rule_id)
    return ScheduledJobOut.model_validate(job)
