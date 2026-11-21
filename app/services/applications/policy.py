"""Regras que autorizam (ou bloqueiam) o envio de uma candidatura.

Centralizar aqui evita que a preparação e o envio usem critérios diferentes.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.timezones import DEFAULT_TIMEZONE, local_start_of_day
from app.integrations.application_submitters.registry import SubmitterRegistry
from app.models.analysis import CoverLetter
from app.models.application import Application, ApplicationApproval
from app.models.enums import (
    ApplicationMode,
    ApplicationStatus,
    ApprovalDecision,
    ConfirmationSource,
)
from app.models.job import JobOpportunity
from app.models.profile import ProfessionalProfile
from app.models.user import User
from app.schemas.application import ApplicationSettingsIn

INTEGRATION_SOURCES = (ConfirmationSource.PLATFORM, ConfirmationSource.SIMULATED)


def answer_from_profile(profile_field: str | None, profile: ProfessionalProfile) -> str | None:
    """Resposta de formulário que pode ser tirada do perfil, ou None se não houver."""
    if profile_field == "salary" and profile.salary_expectation_min is not None:
        return f"{profile.salary_expectation_min} {profile.salary_currency}"
    if profile_field == "availability":
        return profile.availability
    return None


def user_timezone(session: Session, user_id: uuid.UUID) -> str:
    user = session.get(User, user_id)
    return user.timezone if user else DEFAULT_TIMEZONE


def submissions_today(session: Session, user_id: uuid.UUID, now: datetime | None = None) -> int:
    """Envios por integração desde o início do dia (UTC), incluindo os em andamento.

    Um envio em APPLYING ainda não tem submitted_at; sem contá-lo, dois envios
    simultâneos poderiam passar juntos pelo limite.
    """
    start_of_day = local_start_of_day(user_timezone(session, user_id), now)
    return (
        session.scalar(
            select(func.count())
            .select_from(Application)
            .where(
                Application.user_id == user_id,
                or_(
                    (Application.submitted_at >= start_of_day)
                    & Application.confirmation_source.in_(INTEGRATION_SOURCES),
                    Application.status == ApplicationStatus.APPLYING,
                ),
            )
        )
        or 0
    )


def daily_limit_reached(
    session: Session, user_id: uuid.UUID, settings: ApplicationSettingsIn
) -> bool:
    return submissions_today(session, user_id) >= settings.max_daily_submissions


ASSISTED_PENDING_STATUSES = (ApplicationStatus.AWAITING_APPROVAL, ApplicationStatus.READY_TO_APPLY)


@dataclass(frozen=True)
class AssistedQuota:
    platform: str
    limit: int
    applied_today: int
    pending_kits: int

    @property
    def remaining(self) -> int:
        return max(self.limit - self.applied_today - self.pending_kits, 0)


def assisted_quota(
    session: Session, user_id: uuid.UUID, platform: str, settings: ApplicationSettingsIn
) -> AssistedQuota | None:
    """Cota do dia para uma plataforma do modo assistido (None se não for assistida).

    Conta as candidaturas enviadas hoje e os kits já prontos aguardando envio, para
    que a fila nunca passe do que dá para enviar no dia.
    """
    if platform not in settings.assisted_daily_limits:
        return None
    start_of_day = local_start_of_day(user_timezone(session, user_id))
    platform_applications = (
        select(func.count())
        .select_from(Application)
        .join(JobOpportunity, JobOpportunity.id == Application.job_id)
        .where(Application.user_id == user_id, JobOpportunity.provider_key == platform)
    )
    applied_today = session.scalar(
        platform_applications.where(Application.submitted_at >= start_of_day)
    )
    pending = session.scalar(
        platform_applications.where(Application.status.in_(ASSISTED_PENDING_STATUSES))
    )
    return AssistedQuota(
        platform=platform,
        limit=settings.assisted_daily_limits[platform],
        applied_today=applied_today or 0,
        pending_kits=pending or 0,
    )


def has_valid_approval(session: Session, application: Application) -> bool:
    """Há aprovação do usuário para exatamente o texto e o currículo atuais?"""
    latest = session.scalar(
        select(ApplicationApproval)
        .where(ApplicationApproval.application_id == application.id)
        .order_by(ApplicationApproval.created_at.desc())
        .limit(1)
    )
    return (
        latest is not None
        and latest.decision is ApprovalDecision.APPROVED
        and latest.cover_letter_id == application.cover_letter_id
        and latest.resume_id == application.resume_id
    )


def auto_submit_blockers(
    session: Session,
    submitters: SubmitterRegistry,
    application: Application,
    job: JobOpportunity,
    settings: ApplicationSettingsIn,
) -> list[str]:
    """Motivos que impedem o envio sem aprovação. Lista vazia = envio automático permitido."""
    if settings.mode is not ApplicationMode.AUTOMATIC:
        return [f"Modo de candidatura {settings.mode} exige ação do usuário."]

    blockers = []
    submitter = submitters.get(job.provider_key)
    if submitter is None:
        blockers.append("A plataforma não tem integração de envio autorizada.")
    if job.provider_key not in settings.auto_submit_providers:
        blockers.append("A plataforma não está na lista de envio automático do usuário.")
    letter = (
        session.get(CoverLetter, application.cover_letter_id)
        if application.cover_letter_id
        else None
    )
    if letter is None:
        blockers.append("A candidatura não tem texto de apresentação.")
    elif letter.validation_warnings:
        blockers.append("O texto de apresentação tem alertas que precisam de revisão.")
    if submitter and submitter.requires_resume and application.resume_id is None:
        blockers.append("Nenhum currículo disponível para anexar.")
    if application.pending_questions:
        blockers.append("Há perguntas obrigatórias sem resposta no perfil.")
    if daily_limit_reached(session, application.user_id, settings):
        blockers.append("Limite diário de candidaturas atingido.")
    return blockers
