"""Regras que autorizam (ou bloqueiam) o envio de uma candidatura.

Centralizar aqui evita que a preparação e o envio usem critérios diferentes.
"""

import uuid
from datetime import UTC, datetime, time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.integrations.application_submitters.registry import SubmitterRegistry
from app.models.analysis import CoverLetter
from app.models.application import Application, ApplicationApproval
from app.models.enums import ApplicationMode, ApprovalDecision, ConfirmationSource
from app.models.job import JobOpportunity
from app.models.profile import ProfessionalProfile
from app.schemas.application import ApplicationSettingsIn

INTEGRATION_SOURCES = (ConfirmationSource.PLATFORM, ConfirmationSource.SIMULATED)


def answer_from_profile(profile_field: str | None, profile: ProfessionalProfile) -> str | None:
    """Resposta de formulário que pode ser tirada do perfil, ou None se não houver."""
    if profile_field == "salary" and profile.salary_expectation_min is not None:
        return f"{profile.salary_expectation_min} {profile.salary_currency}"
    if profile_field == "availability":
        return profile.availability
    return None


def submissions_today(session: Session, user_id: uuid.UUID) -> int:
    """Envios por integração desde o início do dia (UTC)."""
    start_of_day = datetime.combine(datetime.now(UTC).date(), time.min, tzinfo=UTC)
    return (
        session.scalar(
            select(func.count())
            .select_from(Application)
            .where(
                Application.user_id == user_id,
                Application.submitted_at >= start_of_day,
                Application.confirmation_source.in_(INTEGRATION_SOURCES),
            )
        )
        or 0
    )


def daily_limit_reached(
    session: Session, user_id: uuid.UUID, settings: ApplicationSettingsIn
) -> bool:
    return submissions_today(session, user_id) >= settings.max_daily_submissions


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
