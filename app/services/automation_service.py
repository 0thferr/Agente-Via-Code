"""Cadastro de regras de automação, execução sob demanda e histórico."""

import uuid
from datetime import UTC, datetime

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.automation.runner import compute_next_run, enqueue_job
from app.automation.tasks import TASK_HANDLERS
from app.core.errors import NotFoundError, ValidationError
from app.models.automation import AutomationRule, ScheduledJob
from app.schemas.automation import AutomationRuleCreate, AutomationRuleUpdate, ExecutionFilters
from app.services.audit_service import record_audit_event

SCHEDULE_FIELDS = ("days_of_week", "time_of_day", "timezone", "interval_minutes")


def list_rules(session: Session, user_id: uuid.UUID) -> list[AutomationRule]:
    return list(
        session.scalars(
            select(AutomationRule)
            .where(AutomationRule.user_id == user_id)
            .order_by(AutomationRule.created_at)
        )
    )


def create_rule(session: Session, user_id: uuid.UUID, data: AutomationRuleCreate) -> AutomationRule:
    _ensure_task_available(data)
    rule = AutomationRule(user_id=user_id, **data.model_dump())
    rule.next_run_at = compute_next_run(rule, datetime.now(UTC))
    session.add(rule)
    session.flush()
    record_audit_event(
        session,
        action="automation.created",
        user_id=user_id,
        entity_type="automation_rule",
        entity_id=rule.id,
        details={"task_type": rule.task_type},
    )
    session.commit()
    return rule


def update_rule(
    session: Session, user_id: uuid.UUID, rule_id: uuid.UUID, data: AutomationRuleUpdate
) -> AutomationRule:
    """Atualização parcial, revalidando a regra completa resultante."""
    rule = get_rule(session, user_id, rule_id)
    current = {
        "name": rule.name,
        "task_type": rule.task_type,
        "is_enabled": rule.is_enabled,
        "days_of_week": list(rule.days_of_week),
        "time_of_day": rule.time_of_day,
        "timezone": rule.timezone,
        "interval_minutes": rule.interval_minutes,
        "config": rule.config,
    }
    changes = data.model_dump(exclude_unset=True)
    # Trocar o tipo de agendamento: o campo do outro tipo é limpo automaticamente.
    if changes.get("time_of_day") is not None:
        current["interval_minutes"] = None
    if changes.get("interval_minutes") is not None:
        current["time_of_day"] = None
    try:
        validated = AutomationRuleCreate.model_validate({**current, **changes})
    except PydanticValidationError as exc:
        messages = "; ".join(error["msg"] for error in exc.errors())
        raise ValidationError(f"Regra inválida: {messages}") from exc

    for field, value in validated.model_dump().items():
        setattr(rule, field, value)
    if any(field in changes for field in SCHEDULE_FIELDS) or changes.get("is_enabled"):
        rule.next_run_at = compute_next_run(rule, datetime.now(UTC))
    record_audit_event(
        session,
        action="automation.updated",
        user_id=user_id,
        entity_type="automation_rule",
        entity_id=rule.id,
        details={"fields": sorted(changes)},
    )
    session.commit()
    return rule


def run_now(session: Session, user_id: uuid.UUID, rule_id: uuid.UUID) -> ScheduledJob:
    """Enfileira uma execução imediata. Repetir no mesmo minuto não cria outra."""
    rule = get_rule(session, user_id, rule_id)
    if not rule.is_enabled:
        raise ValidationError("A regra está desativada. Ative-a antes de executar.")
    now = datetime.now(UTC)
    job = enqueue_job(session, rule, now.replace(second=0, microsecond=0), now)
    record_audit_event(
        session,
        action="automation.run_requested",
        user_id=user_id,
        entity_type="automation_rule",
        entity_id=rule.id,
    )
    session.commit()
    return job


def get_rule(session: Session, user_id: uuid.UUID, rule_id: uuid.UUID) -> AutomationRule:
    rule = session.scalar(
        select(AutomationRule).where(
            AutomationRule.id == rule_id, AutomationRule.user_id == user_id
        )
    )
    if rule is None:
        raise NotFoundError("Regra de automação não encontrada.")
    return rule


def list_executions(
    session: Session, user_id: uuid.UUID, filters: ExecutionFilters
) -> tuple[list[ScheduledJob], int]:
    conditions = [ScheduledJob.user_id == user_id]
    if filters.rule_id:
        conditions.append(ScheduledJob.rule_id == filters.rule_id)
    if filters.status:
        conditions.append(ScheduledJob.status == filters.status)
    total = session.scalar(select(func.count()).select_from(ScheduledJob).where(*conditions))
    jobs = session.scalars(
        select(ScheduledJob)
        .where(*conditions)
        .order_by(ScheduledJob.created_at.desc())
        .limit(filters.limit)
        .offset(filters.offset)
    )
    return list(jobs), total or 0


def _ensure_task_available(data: AutomationRuleCreate) -> None:
    if data.task_type not in TASK_HANDLERS:
        raise ValidationError(f"A tarefa {data.task_type} ainda não está disponível.")
