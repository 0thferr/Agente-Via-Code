"""Tarefas executadas pelo worker. Cada uma é idempotente e limitada por execução.

Idempotência vem das garantias que já existem nos serviços: vagas deduplicadas,
uma candidatura por vaga, envio com chave de idempotência. Rodar a mesma tarefa
duas vezes não duplica nada.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, IntegrationError, ValidationError
from app.integrations.application_submitters.registry import SubmitterRegistry
from app.integrations.job_providers.registry import ProviderRegistry
from app.models.analysis import JobAnalysis
from app.models.application import Application
from app.models.enums import (
    AnalysisStatus,
    ApplicationStatus,
    AutomationTask,
    EventActor,
    Recommendation,
)
from app.models.job import JobOpportunity
from app.schemas.application import ApplicationPrepareRequest
from app.schemas.automation import BatchConfig, DiscoverJobsConfig
from app.schemas.job import JobSearchRequest
from app.services import job_service
from app.services.analysis import analysis_service
from app.services.analysis.requirements import RequirementAnalyzer
from app.services.applications import application_service, submission_service
from app.services.cover_letters.writers import CoverLetterWriter

SUMMARY_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class TaskContext:
    providers: ProviderRegistry
    analyzer: RequirementAnalyzer
    writer: CoverLetterWriter
    submitters: SubmitterRegistry


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
    already_analyzed = exists().where(JobAnalysis.job_id == JobOpportunity.id)
    job_ids = session.scalars(
        select(JobOpportunity.id)
        .where(JobOpportunity.user_id == user_id, ~already_analyzed)
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
    """Cria e prepara candidaturas para vagas qualificadas que ainda não têm uma."""
    limit = BatchConfig.model_validate(config).max_items
    has_application = exists().where(Application.job_id == JobAnalysis.job_id)
    job_ids = session.scalars(
        select(JobAnalysis.job_id)
        .where(
            JobAnalysis.user_id == user_id,
            JobAnalysis.status == AnalysisStatus.COMPLETED,
            JobAnalysis.recommendation == Recommendation.QUALIFIED,
            ~has_application,
        )
        .group_by(JobAnalysis.job_id)
        .order_by(func.max(JobAnalysis.created_at).desc())
        .limit(limit)
    ).all()
    counts = {"prepared": 0, "skipped": 0}
    for job_id in job_ids:
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
        except (ValidationError, ConflictError):
            counts["skipped"] += 1
    return counts


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
    return build_activity_summary(session, user_id, since=datetime.now(UTC) - SUMMARY_WINDOW)


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
}
