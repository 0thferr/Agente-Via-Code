"""Envio de candidaturas por integração autorizada, de forma idempotente.

Garantias:
- a linha da candidatura é travada (SELECT ... FOR UPDATE) durante a verificação;
- o status APPLYING e a chave de idempotência são gravados ANTES de chamar a
  plataforma, então uma falha ou reinício no meio do caminho fica visível;
- toda tentativa usa a mesma chave, e a plataforma reconhece reenvios;
- APPLIED só com confirmação devolvida pela plataforma.
"""

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, IntegrationError, ValidationError
from app.integrations.application_submitters.base import (
    ApplicationSubmitter,
    SubmissionError,
    SubmissionOutcomeUnknownError,
    SubmissionPayload,
)
from app.integrations.application_submitters.registry import SubmitterRegistry
from app.models.analysis import CoverLetter
from app.models.application import Application
from app.models.enums import ApplicationMode, ApplicationStatus, EventActor, NotificationEvent
from app.models.job import JobOpportunity
from app.models.profile import Resume
from app.schemas.application import ApplicationSettingsIn
from app.services import job_service, profile_service
from app.services.applications import application_service, policy
from app.services.applications.state_machine import transition
from app.services.audit_service import record_audit_event
from app.services.communication import notification_service

logger = logging.getLogger(__name__)

ALREADY_SUBMITTED_STATUSES = {
    ApplicationStatus.APPLIED,
    ApplicationStatus.ASSESSMENT,
    ApplicationStatus.INTERVIEW,
    ApplicationStatus.OFFER,
    ApplicationStatus.HIRED,
}
SUBMITTABLE_STATUSES = {ApplicationStatus.READY_TO_APPLY, ApplicationStatus.ERROR}


def submit_application(
    session: Session,
    submitters: SubmitterRegistry,
    user_id: uuid.UUID,
    application_id: uuid.UUID,
    *,
    actor: EventActor = EventActor.USER,
) -> Application:
    lock_user_submissions(session, user_id)
    application = application_service.get_application(
        session, user_id, application_id, for_update=True
    )
    if application.status in ALREADY_SUBMITTED_STATUSES:
        return application  # já enviada: repetir o pedido não envia de novo
    if application.status is ApplicationStatus.APPLYING:
        raise ConflictError("Envio em andamento ou com resultado ainda não confirmado.")
    if application.status not in SUBMITTABLE_STATUSES:
        raise ConflictError(f"Candidatura em {application.status} não pode ser enviada.")

    job = job_service.get_job(session, user_id, application.job_id)
    settings = application_service.get_settings(session, user_id)
    submitter = _check_authorization(
        session, submitters, application, job, settings, submitters.get(job.provider_key)
    )

    # Monta o payload antes de mudar o status: um erro aqui não deixa a candidatura
    # presa em APPLYING.
    payload = _build_payload(session, application, job, submitter)
    application.submission_key = application.submission_key or uuid.uuid4()
    application.last_error = None
    transition(session, application, ApplicationStatus.APPLYING, actor=actor)
    session.commit()  # registra a intenção antes de qualquer efeito externo

    try:
        result = submitter.submit(payload, str(application.submission_key))
    except SubmissionOutcomeUnknownError as exc:
        _mark_error(session, application, actor, str(exc), outcome_unknown=True)
        raise IntegrationError(
            "Resultado do envio incerto. Uma nova tentativa usará a mesma chave de "
            "idempotência, sem risco de candidatura duplicada."
        ) from exc
    except SubmissionError as exc:
        _mark_error(session, application, actor, str(exc), outcome_unknown=False)
        raise IntegrationError(f"A plataforma recusou o envio: {exc}") from exc

    application.submission_channel = job.provider_key
    application.confirmation_source = result.confirmation_source
    application.external_confirmation_id = result.confirmation_id
    application.submitted_at = datetime.now(UTC)
    transition(
        session,
        application,
        ApplicationStatus.APPLIED,
        actor=actor,
        event_type="submitted",
        details={
            "confirmation_id": result.confirmation_id,
            "confirmation_source": result.confirmation_source,
        },
    )
    notification_service.notify(
        session,
        user_id,
        NotificationEvent.APPLICATION_SUBMITTED,
        title="Candidatura enviada",
        body=f"{job.title} — {job.company}. Confirmação: {result.confirmation_id}",
        data={"application_id": str(application.id)},
        dedup_key=f"submitted:{application.id}",
    )
    record_audit_event(
        session,
        action="application.submitted",
        user_id=user_id,
        entity_type="application",
        entity_id=application.id,
        details={"channel": job.provider_key, "confirmation_source": result.confirmation_source},
    )
    session.commit()
    return application


def lock_user_submissions(session: Session, user_id: uuid.UUID) -> None:
    """Trava (advisory lock do PostgreSQL) os envios do usuário até o fim da transação.

    Assim, a verificação do limite diário e a gravação de APPLYING de dois envios
    simultâneos acontecem uma de cada vez. A trava é liberada no commit que grava
    APPLYING, e daí em diante o envio em andamento já conta no limite.
    """
    session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(str(user_id), 0))))


def _check_authorization(
    session: Session,
    submitters: SubmitterRegistry,
    application: Application,
    job: JobOpportunity,
    settings: ApplicationSettingsIn,
    submitter: ApplicationSubmitter | None,
) -> ApplicationSubmitter:
    """Verifica permissões antes de qualquer operação externa."""
    if settings.mode is ApplicationMode.MANUAL:
        raise ValidationError(
            "Modo manual: candidate-se na plataforma e registre com /mark-applied."
        )
    if submitter is None:
        raise ValidationError(
            "Esta plataforma não tem integração de envio autorizada. Candidate-se na "
            "plataforma e registre com /mark-applied."
        )
    if application.pending_questions:
        raise ValidationError("Há perguntas obrigatórias sem resposta.")
    if policy.daily_limit_reached(session, application.user_id, settings):
        raise ValidationError("Limite diário de candidaturas atingido.")
    approved = policy.has_valid_approval(session, application)
    auto_allowed = not policy.auto_submit_blockers(session, submitters, application, job, settings)
    if not (approved or auto_allowed):
        raise ValidationError("Envio não autorizado: a candidatura precisa da sua aprovação.")
    return submitter


def _build_payload(
    session: Session,
    application: Application,
    job: JobOpportunity,
    submitter: ApplicationSubmitter,
) -> SubmissionPayload:
    profile = profile_service.get_profile(session, application.user_id)
    letter = (
        session.get(CoverLetter, application.cover_letter_id)
        if application.cover_letter_id
        else None
    )
    resume = session.get(Resume, application.resume_id) if application.resume_id else None
    answers: dict[str, str] = {}
    for question in submitter.required_questions(job.external_id):
        value = application.answers.get(question.id) or policy.answer_from_profile(
            question.profile_field, profile
        )
        if value:
            answers[question.id] = value
    return SubmissionPayload(
        job_external_id=job.external_id,
        job_url=job.url,
        candidate_name=profile.full_name,
        candidate_email=profile.contact_email,
        cover_letter=letter.content if letter else None,
        resume_storage_key=resume.storage_key if resume else None,
        answers=answers,
    )


def _mark_error(
    session: Session,
    application: Application,
    actor: EventActor,
    error: str,
    *,
    outcome_unknown: bool,
) -> None:
    logger.warning(
        "application_submission_failed",
        extra={"application_id": str(application.id), "outcome_unknown": outcome_unknown},
    )
    application.last_error = error[:500]
    transition(
        session,
        application,
        ApplicationStatus.ERROR,
        actor=actor,
        event_type="submission_failed",
        details={"outcome_unknown": outcome_unknown},
    )
    notification_service.notify(
        session,
        application.user_id,
        NotificationEvent.SUBMISSION_FAILED,
        title="Falha no envio da candidatura",
        body=error[:300],
        data={"application_id": str(application.id), "outcome_unknown": outcome_unknown},
    )
    record_audit_event(
        session,
        action="application.submission_failed",
        user_id=application.user_id,
        entity_type="application",
        entity_id=application.id,
        details={"outcome_unknown": outcome_unknown},
    )
    session.commit()
