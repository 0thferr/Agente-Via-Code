"""Tarefas executadas pelo worker. Cada uma é idempotente e limitada por execução.

Idempotência vem das garantias que já existem nos serviços: vagas deduplicadas,
uma candidatura por vaga, envio com chave de idempotência. Rodar a mesma tarefa
duas vezes não duplica nada.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.core.crypto import SecretCipher
from app.core.errors import ConflictError, IntegrationError, ValidationError
from app.core.timezones import local_date_iso
from app.integrations.application_submitters.registry import SubmitterRegistry
from app.integrations.email.base import EmailClient
from app.integrations.job_providers.registry import ProviderRegistry
from app.integrations.notifications.channels import ChannelRegistry
from app.models.analysis import JobAnalysis
from app.models.application import Application
from app.models.enums import (
    AnalysisStatus,
    ApplicationStatus,
    AutomationTask,
    EmailProvider,
    EventActor,
    NotificationEvent,
    Recommendation,
)
from app.models.job import JobOpportunity
from app.schemas.application import ApplicationPrepareRequest
from app.schemas.automation import BatchConfig, DiscoverJobsConfig
from app.schemas.job import JobSearchRequest
from app.services import job_service
from app.services.analysis import analysis_service
from app.services.analysis.requirements import RequirementAnalyzer
from app.services.applications import application_service, policy, submission_service
from app.services.communication import email_service, notification_service
from app.services.cover_letters.writers import CoverLetterWriter

SUMMARY_WINDOW = timedelta(hours=24)
MAX_ANALYSIS_FAILURES = 3


@dataclass(frozen=True)
class TaskContext:
    providers: ProviderRegistry
    analyzer: RequirementAnalyzer
    writer: CoverLetterWriter
    submitters: SubmitterRegistry
    email_clients: dict[EmailProvider, EmailClient] = field(default_factory=dict)
    cipher: SecretCipher = field(default_factory=lambda: SecretCipher(None))
    channels: ChannelRegistry = field(default_factory=lambda: ChannelRegistry([]))
    email_lookback: timedelta = timedelta(days=14)


TaskHandler = Callable[[Session, TaskContext, uuid.UUID, dict[str, Any]], dict[str, Any]]


def discover_jobs(
    session: Session, context: TaskContext, user_id: uuid.UUID, config: dict[str, Any]
) -> dict[str, Any]:
    settings = DiscoverJobsConfig.model_validate(config)
    request = JobSearchRequest(
        filters=settings.filters, source_ids=settings.source_ids, incremental=True
    )
    response = job_service.search_jobs(session, context.providers, user_id, request)
    return {
        "created": len(response.created_job_ids),
        "duplicates": sum(result.duplicates for result in response.results),
        "source_errors": [
            {"provider": result.provider_key, "error": result.error}
            for result in response.results
            if result.error
        ],
    }


def analyze_jobs(
    session: Session, context: TaskContext, user_id: uuid.UUID, config: dict[str, Any]
) -> dict[str, Any]:
    """Analisa vagas que ainda não têm nenhuma análise."""
    limit = BatchConfig.model_validate(config).max_items
    # Análises que falharam não contam como "analisada": a vaga volta para a fila,
    # até MAX_ANALYSIS_FAILURES tentativas (para uma vaga problemática não travar as demais).
    already_analyzed = exists().where(
        JobAnalysis.job_id == JobOpportunity.id, JobAnalysis.status != AnalysisStatus.FAILED
    )
    failures = (
        select(func.count())
        .where(JobAnalysis.job_id == JobOpportunity.id, JobAnalysis.status == AnalysisStatus.FAILED)
        .scalar_subquery()
    )
    job_ids = session.scalars(
        select(JobOpportunity.id)
        .where(
            JobOpportunity.user_id == user_id,
            ~already_analyzed,
            failures < MAX_ANALYSIS_FAILURES,
        )
        .order_by(JobOpportunity.created_at.desc())
        .limit(limit)
    ).all()
    counts = {"analyzed": 0, "qualified": 0, "rejected": 0, "pending": 0}
    for job_id in job_ids:
        # IntegrationError (ex.: IA fora do ar) interrompe a execução e vai para
        # retentativa: insistir em cada vaga só aumentaria a carga no provedor.
        analysis = analysis_service.analyze_job(session, context.analyzer, user_id, job_id)
        counts["analyzed"] += 1
        counts[_recommendation_bucket(analysis)] += 1
    return counts


def prepare_applications(
    session: Session, context: TaskContext, user_id: uuid.UUID, config: dict[str, Any]
) -> dict[str, Any]:
    """Cria e prepara candidaturas para as vagas qualificadas mais compatíveis.

    Nas plataformas do modo assistido (ex.: LinkedIn, Gupy), respeita o limite diário:
    vagas além da cota ficam para os próximos dias.
    """
    limit = BatchConfig.model_validate(config).max_items
    settings = application_service.get_settings(session, user_id)
    rows = session.execute(
        select(JobAnalysis.job_id, JobOpportunity.provider_key)
        .join(JobOpportunity, JobOpportunity.id == JobAnalysis.job_id)
        .outerjoin(Application, Application.job_id == JobAnalysis.job_id)
        .where(
            JobAnalysis.user_id == user_id,
            JobAnalysis.status == AnalysisStatus.COMPLETED,
            JobAnalysis.recommendation == Recommendation.QUALIFIED,
            # Sem candidatura ainda, ou criada mas não preparada (ex.: cota de ontem).
            or_(Application.id.is_(None), Application.status == ApplicationStatus.DISCOVERED),
        )
        .group_by(JobAnalysis.job_id, JobOpportunity.provider_key)
        .order_by(func.max(JobAnalysis.score).desc())
        .limit(limit)
    ).all()
    counts = {"prepared": 0, "skipped": 0, "over_daily_limit": 0}
    assisted_ready: dict[str, int] = {}
    for job_id, platform in rows:
        quota = policy.assisted_quota(session, user_id, platform, settings)
        if quota is not None and quota.remaining == 0:
            counts["over_daily_limit"] += 1
            continue
        application, _ = application_service.create_application(session, user_id, job_id)
        try:
            application_service.prepare_application(
                session,
                context.analyzer,
                context.writer,
                context.submitters,
                user_id,
                application.id,
                ApplicationPrepareRequest(),
            )
            counts["prepared"] += 1
            if quota is not None:
                assisted_ready[platform] = assisted_ready.get(platform, 0) + 1
        except (ValidationError, ConflictError):
            counts["skipped"] += 1
    if assisted_ready:
        _notify_assisted_queue(session, user_id, assisted_ready)
    return counts


def _notify_assisted_queue(session: Session, user_id: uuid.UUID, ready: dict[str, int]) -> None:
    summary = ", ".join(f"{count} no {platform}" for platform, count in sorted(ready.items()))
    notification_service.notify(
        session,
        user_id,
        NotificationEvent.ASSISTED_QUEUE_READY,
        title="Candidaturas prontas para você enviar",
        body=f"Kits prontos: {summary}. Abra cada kit, envie na plataforma e registre o envio.",
        data={"ready": ready},
    )
    session.commit()


def submit_applications(
    session: Session, context: TaskContext, user_id: uuid.UUID, config: dict[str, Any]
) -> dict[str, Any]:
    """Envia candidaturas prontas. As regras de autorização são as mesmas do envio manual."""
    limit = BatchConfig.model_validate(config).max_items
    application_ids = session.scalars(
        select(Application.id)
        .where(
            Application.user_id == user_id,
            Application.status == ApplicationStatus.READY_TO_APPLY,
        )
        .order_by(Application.updated_at)
        .limit(limit)
    ).all()
    counts = {"submitted": 0, "not_authorized": 0, "failed": 0}
    for application_id in application_ids:
        try:
            submission_service.submit_application(
                session, context.submitters, user_id, application_id, actor=EventActor.AGENT
            )
            counts["submitted"] += 1
        except (ValidationError, ConflictError):
            counts["not_authorized"] += 1
        except IntegrationError:
            counts["failed"] += 1
    return counts


def daily_summary(
    session: Session, context: TaskContext, user_id: uuid.UUID, config: dict[str, Any]
) -> dict[str, Any]:
    now = datetime.now(UTC)
    summary = build_activity_summary(session, user_id, since=now - SUMMARY_WINDOW)
    notification_service.notify(
        session,
        user_id,
        NotificationEvent.DAILY_SUMMARY,
        title="Resumo diário do JobPilot",
        body=(
            f"Vagas encontradas: {summary['jobs_discovered']} · "
            f"analisadas: {summary['jobs_analyzed']} · "
            f"candidaturas enviadas: {summary['applications_submitted']} · "
            f"aguardando aprovação: {summary['awaiting_approval']}"
        ),
        data=summary,
        dedup_key=f"daily_summary:{local_date_iso(policy.user_timezone(session, user_id), now)}",
    )
    session.commit()
    return summary


def check_emails(
    session: Session, context: TaskContext, user_id: uuid.UUID, config: dict[str, Any]
) -> dict[str, Any]:
    return email_service.sync_user_accounts(
        session,
        context.email_clients,
        context.cipher,
        user_id,
        context.email_lookback,
        datetime.now(UTC),
    )


def check_deadlines(
    session: Session, context: TaskContext, user_id: uuid.UUID, config: dict[str, Any]
) -> dict[str, Any]:
    created = email_service.notify_upcoming_deadlines(session, user_id, datetime.now(UTC))
    return {"deadline_notifications": created}


def build_activity_summary(session: Session, user_id: uuid.UUID, since: datetime) -> dict[str, Any]:
    def count(model, *conditions) -> int:
        return session.scalar(select(func.count()).select_from(model).where(*conditions)) or 0

    return {
        "since": since.isoformat(),
        "jobs_discovered": count(
            JobOpportunity, JobOpportunity.user_id == user_id, JobOpportunity.created_at >= since
        ),
        "jobs_analyzed": count(
            JobAnalysis, JobAnalysis.user_id == user_id, JobAnalysis.created_at >= since
        ),
        "applications_submitted": count(
            Application, Application.user_id == user_id, Application.submitted_at >= since
        ),
        "awaiting_approval": count(
            Application,
            Application.user_id == user_id,
            Application.status == ApplicationStatus.AWAITING_APPROVAL,
        ),
    }


def _recommendation_bucket(analysis: JobAnalysis) -> str:
    if analysis.recommendation is Recommendation.QUALIFIED:
        return "qualified"
    if analysis.recommendation is Recommendation.REJECTED:
        return "rejected"
    return "pending"


TASK_HANDLERS: dict[AutomationTask, TaskHandler] = {
    AutomationTask.DISCOVER_JOBS: discover_jobs,
    AutomationTask.ANALYZE_JOBS: analyze_jobs,
    AutomationTask.PREPARE_APPLICATIONS: prepare_applications,
    AutomationTask.SUBMIT_APPLICATIONS: submit_applications,
    AutomationTask.DAILY_SUMMARY: daily_summary,
    AutomationTask.CHECK_EMAILS: check_emails,
    AutomationTask.CHECK_DEADLINES: check_deadlines,
}
