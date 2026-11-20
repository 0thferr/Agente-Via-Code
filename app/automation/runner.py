"""Execução das automações: enfileirar, reservar, executar, repetir e recuperar.

Seguro com várias instâncias do worker ao mesmo tempo:
- enfileirar usa (rule_id, scheduled_for) único + ON CONFLICT DO NOTHING;
- reservar usa SELECT ... FOR UPDATE SKIP LOCKED (cada job vai para um só worker);
- a reserva tem prazo (lease): se o worker morrer, outro retoma depois do prazo.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from app.automation.schedule import next_run_at
from app.automation.tasks import TASK_HANDLERS, TaskContext
from app.core.errors import DomainError, IntegrationError
from app.models.application import Application
from app.models.automation import AutomationRule, ExecutionLog, ScheduledJob
from app.models.enums import ApplicationStatus, EventActor, JobRunStatus
from app.services.applications.state_machine import transition
from app.services.audit_service import record_audit_event

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 3
RETRY_BASE_DELAY = timedelta(minutes=1)
MAX_ERROR_LENGTH = 500


@dataclass
class TickResult:
    enqueued: int = 0
    executed: int = 0
    recovered_submissions: int = 0


def compute_next_run(rule: AutomationRule, after: datetime) -> datetime:
    return next_run_at(
        after=after,
        timezone=rule.timezone,
        days_of_week=list(rule.days_of_week),
        time_of_day=rule.time_of_day,
        interval_minutes=rule.interval_minutes,
    )


def enqueue_job(
    session: Session, rule: AutomationRule, scheduled_for: datetime, now: datetime
) -> ScheduledJob:
    """Cria a execução lógica da regra para o horário dado (idempotente)."""
    session.execute(
        insert(ScheduledJob)
        .values(
            rule_id=rule.id,
            user_id=rule.user_id,
            task_type=rule.task_type,
            scheduled_for=scheduled_for,
            status=JobRunStatus.PENDING,
            attempts=0,
            max_attempts=DEFAULT_MAX_ATTEMPTS,
            next_attempt_at=now,
        )
        .on_conflict_do_nothing(constraint="uq_scheduled_jobs_rule_slot")
    )
    return session.scalars(
        select(ScheduledJob).where(
            ScheduledJob.rule_id == rule.id, ScheduledJob.scheduled_for == scheduled_for
        )
    ).one()


def enqueue_due_rules(session: Session, now: datetime) -> int:
    rules = session.scalars(
        select(AutomationRule)
        .where(AutomationRule.is_enabled.is_(True), AutomationRule.next_run_at <= now)
        .with_for_update(skip_locked=True)
    ).all()
    for rule in rules:
        enqueue_job(session, rule, rule.next_run_at, now)
        rule.last_run_at = now
        rule.next_run_at = compute_next_run(rule, now)
    session.commit()
    return len(rules)


def claim_next_job(
    session: Session, worker_id: str, now: datetime, lease: timedelta
) -> ScheduledJob | None:
    job = session.scalars(
        select(ScheduledJob)
        .where(
            or_(
                (ScheduledJob.status == JobRunStatus.PENDING)
                & (ScheduledJob.next_attempt_at <= now),
                # Reserva vencida: o worker anterior provavelmente morreu.
                (ScheduledJob.status == JobRunStatus.RUNNING)
                & (ScheduledJob.lease_expires_at < now),
            )
        )
        .order_by(ScheduledJob.next_attempt_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()
    if job is None:
        return None
    if job.attempts >= job.max_attempts:
        _finish(session, job, JobRunStatus.FAILED, now, "Tentativas esgotadas (reserva vencida).")
        return None
    job.status = JobRunStatus.RUNNING
    job.attempts += 1
    job.locked_by = worker_id
    job.lease_expires_at = now + lease
    session.commit()
    return job


def execute_job(session: Session, context: TaskContext, job: ScheduledJob, now: datetime) -> None:
    started_at = now
    handler = TASK_HANDLERS.get(job.task_type)
    rule = session.get(AutomationRule, job.rule_id)
    try:
        if handler is None:
            raise DomainError(f"Tarefa {job.task_type} não está disponível.")
        summary = handler(session, context, job.user_id, rule.config if rule else {})
    except Exception as exc:  # qualquer falha da tarefa é registrada e tratada abaixo
        session.rollback()
        _handle_failure(session, job, exc, started_at, now)
        return

    _log(session, job, JobRunStatus.SUCCEEDED, started_at, summary=summary)
    _finish(session, job, JobRunStatus.SUCCEEDED, now)


def recover_stale_submissions(session: Session, now: datetime, stale_after: timedelta) -> int:
    """Candidaturas presas em APPLYING (ex.: processo caiu durante o envio) vão para ERROR.

    O resultado é tratado como incerto: uma nova tentativa reutiliza a mesma chave
    de idempotência, então a plataforma não cria candidatura duplicada.
    """
    stale = session.scalars(
        select(Application)
        .where(
            Application.status == ApplicationStatus.APPLYING,
            Application.updated_at < now - stale_after,
        )
        .with_for_update(skip_locked=True)
    ).all()
    for application in stale:
        application.last_error = "Envio interrompido; resultado incerto. Verifique e tente de novo."
        transition(
            session,
            application,
            ApplicationStatus.ERROR,
            actor=EventActor.SYSTEM,
            event_type="submission_recovered",
            details={"outcome_unknown": True},
        )
        record_audit_event(
            session,
            action="application.submission_recovered",
            user_id=application.user_id,
            entity_type="application",
            entity_id=application.id,
        )
    session.commit()
    return len(stale)


def run_tick(
    session_factory: sessionmaker[Session],
    context: TaskContext,
    *,
    worker_id: str,
    now: datetime,
    lease: timedelta,
    stale_submission_after: timedelta,
    max_jobs: int,
) -> TickResult:
    result = TickResult()
    with session_factory() as session:
        result.recovered_submissions = recover_stale_submissions(
            session, now, stale_submission_after
        )
        result.enqueued = enqueue_due_rules(session, now)
    for _ in range(max_jobs):
        with session_factory() as session:
            job = claim_next_job(session, worker_id, now, lease)
            if job is None:
                break
            execute_job(session, context, job, now)
            result.executed += 1
    return result


def _handle_failure(
    session: Session, job: ScheduledJob, error: Exception, started_at: datetime, now: datetime
) -> None:
    message = str(error)[:MAX_ERROR_LENGTH] or type(error).__name__
    # Erros de domínio (configuração inválida, perfil ausente...) não melhoram com
    # nova tentativa; falhas de integração e inesperadas, sim.
    retryable = isinstance(error, IntegrationError) or not isinstance(error, DomainError)
    logger.warning(
        "automation_job_failed",
        extra={"job_id": str(job.id), "task": job.task_type, "retryable": retryable},
        exc_info=not isinstance(error, DomainError),
    )
    _log(session, job, JobRunStatus.FAILED, started_at, error=message)
    if retryable and job.attempts < job.max_attempts:
        job.status = JobRunStatus.PENDING
        job.next_attempt_at = now + RETRY_BASE_DELAY * (2 ** (job.attempts - 1))
        job.lease_expires_at = None
        job.locked_by = None
        job.last_error = message
        session.commit()
        return
    _finish(session, job, JobRunStatus.FAILED, now, message)


def _finish(
    session: Session,
    job: ScheduledJob,
    status: JobRunStatus,
    now: datetime,
    error: str | None = None,
) -> None:
    job.status = status
    job.finished_at = now
    job.lease_expires_at = None
    job.last_error = error
    session.commit()


def _log(
    session: Session,
    job: ScheduledJob,
    status: JobRunStatus,
    started_at: datetime,
    *,
    summary: dict | None = None,
    error: str | None = None,
) -> None:
    session.add(
        ExecutionLog(
            scheduled_job_id=job.id,
            user_id=job.user_id,
            task_type=job.task_type,
            attempt=job.attempts,
            status=status,
            summary=summary or {},
            error=error,
            started_at=started_at,
        )
    )
