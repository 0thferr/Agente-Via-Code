"""Indicadores do dashboard, sempre calculados a partir dos registros reais do banco.

Convenções:
- métricas de vagas usam a data em que a vaga foi encontrada (created_at);
- "enviadas" usam submitted_at; etapas alcançadas (entrevista, proposta...) usam a
  linha do tempo da candidatura, então contam mesmo se a candidatura já avançou;
- a análise considerada é a mais recente concluída de cada vaga.
"""

import uuid
from datetime import datetime

from sqlalchemy import Select, and_, exists, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from app.models.analysis import JobAnalysis
from app.models.application import Application, ApplicationEvent
from app.models.automation import ExecutionLog
from app.models.enums import AnalysisStatus, ApplicationStatus, JobRunStatus, Recommendation
from app.models.job import JobOpportunity
from app.schemas.dashboard import (
    DashboardFilters,
    FunnelOut,
    FunnelStage,
    OverviewOut,
    PlatformMetrics,
    TimelineFilters,
    TimelineOut,
    TimelinePoint,
)

PROGRESS_STAGES = {
    "assessment": ApplicationStatus.ASSESSMENT,
    "interview": ApplicationStatus.INTERVIEW,
    "offer": ApplicationStatus.OFFER,
    "hired": ApplicationStatus.HIRED,
}


def overview(session: Session, user_id: uuid.UUID, filters: DashboardFilters) -> OverviewOut:
    job_stats = _job_stats(session, user_id, filters)
    submitted = _count_applications(session, user_id, filters, submitted_in_range=True)
    reached = {
        name: _count_applications(session, user_id, filters, reached=status)
        for name, status in PROGRESS_STAGES.items()
    }
    awaiting = _count_applications(
        session, user_id, filters, current_status=ApplicationStatus.AWAITING_APPROVAL
    )
    return OverviewOut(
        date_from=filters.date_from,
        date_to=filters.date_to,
        jobs_found=job_stats["found"],
        jobs_analyzed=job_stats["analyzed"],
        jobs_qualified=job_stats["qualified"],
        jobs_rejected=job_stats["rejected"],
        average_score=job_stats["average_score"],
        applications_awaiting_approval=awaiting,
        applications_submitted=submitted,
        assessments=reached["assessment"],
        interviews=reached["interview"],
        offers=reached["offer"],
        hires=reached["hired"],
        interview_rate=_rate(reached["interview"], submitted),
        offer_rate=_rate(reached["offer"], submitted),
    )


def funnel(session: Session, user_id: uuid.UUID, filters: DashboardFilters) -> FunnelOut:
    job_stats = _job_stats(session, user_id, filters)
    counts = [
        ("jobs_found", job_stats["found"]),
        ("jobs_analyzed", job_stats["analyzed"]),
        ("jobs_qualified", job_stats["qualified"]),
        ("applications_created", _count_applications(session, user_id, filters)),
        (
            "applications_submitted",
            _count_applications(session, user_id, filters, submitted_in_range=True),
        ),
        *(
            (name, _count_applications(session, user_id, filters, reached=status))
            for name, status in PROGRESS_STAGES.items()
        ),
    ]
    stages = []
    previous: int | None = None
    for name, count in counts:
        stages.append(
            FunnelStage(
                stage=name,
                count=count,
                rate_from_previous=_rate(count, previous) if previous is not None else None,
            )
        )
        previous = count
    return FunnelOut(date_from=filters.date_from, date_to=filters.date_to, stages=stages)


def platforms(
    session: Session, user_id: uuid.UUID, filters: DashboardFilters
) -> list[PlatformMetrics]:
    latest = _latest_analysis_subquery(user_id)
    job_rows = session.execute(
        _filtered_jobs(user_id, filters, latest)
        .with_only_columns(
            JobOpportunity.provider_key,
            func.count(JobOpportunity.id),
            func.count().filter(latest.c.recommendation == Recommendation.QUALIFIED),
            func.avg(latest.c.score),
        )
        .group_by(JobOpportunity.provider_key)
    ).all()

    submitted_by_provider = _applications_by_provider(session, user_id, filters, reached=None)
    interviews_by_provider = _applications_by_provider(
        session, user_id, filters, reached=ApplicationStatus.INTERVIEW
    )
    providers = {row[0] for row in job_rows} | set(submitted_by_provider)
    jobs_by_provider = {row[0]: row for row in job_rows}
    result = []
    for provider in sorted(providers):
        row = jobs_by_provider.get(provider)
        result.append(
            PlatformMetrics(
                provider_key=provider,
                jobs_found=row[1] if row else 0,
                jobs_qualified=row[2] if row else 0,
                average_score=_round(row[3]) if row else None,
                applications_submitted=submitted_by_provider.get(provider, 0),
                interviews=interviews_by_provider.get(provider, 0),
            )
        )
    return result


def timeline(session: Session, user_id: uuid.UUID, filters: TimelineFilters) -> TimelineOut:
    period = filters.period.value
    points: dict[datetime, TimelinePoint] = {}

    def bucket(column: ColumnElement) -> ColumnElement:
        # Agrupa sempre em UTC, independentemente do fuso configurado no banco.
        return func.date_trunc(period, column, "UTC").label("bucket")

    latest = _latest_analysis_subquery(user_id)
    jobs_query = _filtered_jobs(user_id, filters, latest)
    found_bucket = bucket(JobOpportunity.created_at)
    for start, count in session.execute(
        jobs_query.with_only_columns(found_bucket, func.count()).group_by(found_bucket)
    ):
        _point(points, start).jobs_found = count

    submitted_bucket = bucket(Application.submitted_at)
    for start, count in session.execute(
        _filtered_applications(user_id, filters)
        .where(Application.submitted_at.between(filters.date_from, filters.date_to))
        .with_only_columns(submitted_bucket, func.count())
        .group_by(submitted_bucket)
    ):
        _point(points, start).applications_submitted = count

    interview_bucket = bucket(ApplicationEvent.created_at)
    for start, count in session.execute(
        _filtered_applications(user_id, filters)
        .join(ApplicationEvent, ApplicationEvent.application_id == Application.id)
        .where(
            ApplicationEvent.to_status == ApplicationStatus.INTERVIEW,
            ApplicationEvent.created_at.between(filters.date_from, filters.date_to),
        )
        .with_only_columns(interview_bucket, func.count(func.distinct(Application.id)))
        .group_by(interview_bucket)
    ):
        _point(points, start).interviews = count

    run_bucket = bucket(ExecutionLog.started_at)
    for start, status, count in session.execute(
        select(run_bucket, ExecutionLog.status, func.count())
        .where(
            ExecutionLog.user_id == user_id,
            ExecutionLog.started_at.between(filters.date_from, filters.date_to),
        )
        .group_by(run_bucket, ExecutionLog.status)
    ):
        point = _point(points, start)
        if status is JobRunStatus.SUCCEEDED:
            point.agent_runs_succeeded = count
        else:
            point.agent_runs_failed += count

    return TimelineOut(
        period=filters.period,
        date_from=filters.date_from,
        date_to=filters.date_to,
        points=[points[key] for key in sorted(points)],
    )


# --- Consultas base -------------------------------------------------------------------


def _latest_analysis_subquery(user_id: uuid.UUID):
    """Análise concluída mais recente de cada vaga.

    Usa row_number() (window function): numera as análises de cada vaga da mais
    nova para a mais antiga e fica só com a primeira.
    """
    position = (
        func.row_number()
        .over(partition_by=JobAnalysis.job_id, order_by=JobAnalysis.created_at.desc())
        .label("position")
    )
    ranked = (
        select(JobAnalysis.job_id, JobAnalysis.score, JobAnalysis.recommendation, position)
        .where(JobAnalysis.user_id == user_id, JobAnalysis.status == AnalysisStatus.COMPLETED)
        .subquery()
    )
    return (
        select(ranked.c.job_id, ranked.c.score, ranked.c.recommendation)
        .where(ranked.c.position == 1)
        .subquery()
    )


def _job_conditions(user_id: uuid.UUID, filters: DashboardFilters) -> list[ColumnElement]:
    conditions = [JobOpportunity.user_id == user_id]
    if filters.provider_key:
        conditions.append(JobOpportunity.provider_key == filters.provider_key)
    if filters.company:
        conditions.append(JobOpportunity.company.icontains(filters.company, autoescape=True))
    if filters.title:
        conditions.append(JobOpportunity.title.icontains(filters.title, autoescape=True))
    return conditions


def _filtered_jobs(user_id: uuid.UUID, filters: DashboardFilters, latest) -> Select:
    query = (
        select(JobOpportunity.id)
        .outerjoin(latest, latest.c.job_id == JobOpportunity.id)
        .where(
            *_job_conditions(user_id, filters),
            JobOpportunity.created_at.between(filters.date_from, filters.date_to),
        )
    )
    if filters.min_score is not None:
        query = query.where(latest.c.score >= filters.min_score)
    return query


def _job_stats(session: Session, user_id: uuid.UUID, filters: DashboardFilters) -> dict:
    latest = _latest_analysis_subquery(user_id)
    row = session.execute(
        _filtered_jobs(user_id, filters, latest).with_only_columns(
            func.count(JobOpportunity.id),
            func.count(latest.c.job_id),
            func.count().filter(latest.c.recommendation == Recommendation.QUALIFIED),
            func.count().filter(latest.c.recommendation == Recommendation.REJECTED),
            func.avg(latest.c.score),
        )
    ).one()
    return {
        "found": row[0],
        "analyzed": row[1],
        "qualified": row[2],
        "rejected": row[3],
        "average_score": _round(row[4]),
    }


def _filtered_applications(user_id: uuid.UUID, filters: DashboardFilters) -> Select:
    query = (
        select(Application.id)
        .join(JobOpportunity, JobOpportunity.id == Application.job_id)
        .where(Application.user_id == user_id, *_job_conditions(user_id, filters))
    )
    if filters.status:
        query = query.where(Application.status == filters.status)
    if filters.min_score is not None:
        query = query.where(Application.compatibility_score >= filters.min_score)
    return query


def _count_applications(
    session: Session,
    user_id: uuid.UUID,
    filters: DashboardFilters,
    *,
    submitted_in_range: bool = False,
    reached: ApplicationStatus | None = None,
    current_status: ApplicationStatus | None = None,
) -> int:
    query = _filtered_applications(user_id, filters)
    if submitted_in_range:
        query = query.where(Application.submitted_at.between(filters.date_from, filters.date_to))
    elif reached is not None:
        query = query.where(_reached(reached, filters))
    elif current_status is not None:
        query = query.where(Application.status == current_status)
    else:
        query = query.where(Application.created_at.between(filters.date_from, filters.date_to))
    return session.scalar(query.with_only_columns(func.count(Application.id))) or 0


def _applications_by_provider(
    session: Session,
    user_id: uuid.UUID,
    filters: DashboardFilters,
    *,
    reached: ApplicationStatus | None,
) -> dict[str, int]:
    query = _filtered_applications(user_id, filters)
    if reached is None:
        query = query.where(Application.submitted_at.between(filters.date_from, filters.date_to))
    else:
        query = query.where(_reached(reached, filters))
    rows = session.execute(
        query.with_only_columns(JobOpportunity.provider_key, func.count(Application.id)).group_by(
            JobOpportunity.provider_key
        )
    )
    return {provider: count for provider, count in rows}


def _reached(status: ApplicationStatus, filters: DashboardFilters) -> ColumnElement:
    """A candidatura passou por este status dentro do período (pela linha do tempo)."""
    return exists().where(
        and_(
            ApplicationEvent.application_id == Application.id,
            ApplicationEvent.to_status == status,
            ApplicationEvent.created_at.between(filters.date_from, filters.date_to),
        )
    )


def _point(points: dict[datetime, TimelinePoint], start: datetime) -> TimelinePoint:
    if start not in points:
        points[start] = TimelinePoint(period_start=start)
    return points[start]


def _rate(numerator: int, denominator: int | None) -> float | None:
    return round(numerator / denominator, 3) if denominator else None


def _round(value) -> float | None:
    return round(float(value), 1) if value is not None else None
