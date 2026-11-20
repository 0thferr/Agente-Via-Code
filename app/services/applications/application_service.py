"""Ciclo de vida da candidatura: criação, preparação, aprovação e acompanhamento.

O envio por integração fica em submission_service.py.
"""

import uuid
from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, IntegrationError, NotFoundError, ValidationError
from app.integrations.application_submitters.registry import SubmitterRegistry
from app.models.analysis import CoverLetter, JobAnalysis
from app.models.application import (
    Application,
    ApplicationApproval,
    ApplicationEvent,
    ApplicationSettings,
)
from app.models.enums import (
    AnalysisStatus,
    ApplicationStatus,
    ApprovalDecision,
    ConfirmationSource,
    EventActor,
    NotificationEvent,
    Recommendation,
    RequirementStatus,
    ResumeImportStatus,
)
from app.models.job import JobOpportunity
from app.models.profile import ProfessionalProfile, Resume
from app.schemas.analysis import CoverLetterGenerateRequest
from app.schemas.application import (
    ApplicationApproveRequest,
    ApplicationListFilters,
    ApplicationPrepareRequest,
    ApplicationSettingsIn,
)
from app.services import job_service, profile_service
from app.services.analysis import analysis_service
from app.services.analysis.requirements import RequirementAnalyzer
from app.services.applications import policy
from app.services.applications.state_machine import (
    PROGRESS_STATUSES,
    record_event,
    transition,
)
from app.services.audit_service import record_audit_event
from app.services.communication import notification_service
from app.services.cover_letters import cover_letter_service
from app.services.cover_letters.writers import CoverLetterWriter

PREPARABLE_STATUSES = {ApplicationStatus.DISCOVERED, ApplicationStatus.REJECTED}
DECLINABLE_STATUSES = {
    ApplicationStatus.DISCOVERED,
    ApplicationStatus.REJECTED,
    ApplicationStatus.QUALIFIED,
    ApplicationStatus.AWAITING_APPROVAL,
    ApplicationStatus.READY_TO_APPLY,
    ApplicationStatus.ERROR,
}
MANUALLY_APPLICABLE_STATUSES = {
    ApplicationStatus.AWAITING_APPROVAL,
    ApplicationStatus.READY_TO_APPLY,
    ApplicationStatus.ERROR,
}
MANUAL_CHANNEL = "manual"
MAX_HIGHLIGHTS = 10


# --- Configurações --------------------------------------------------------------------


def get_settings(session: Session, user_id: uuid.UUID) -> ApplicationSettingsIn:
    stored = session.get(ApplicationSettings, user_id)
    if stored is None:
        return ApplicationSettingsIn()
    return ApplicationSettingsIn.model_validate(stored, from_attributes=True)


def save_settings(
    session: Session, user_id: uuid.UUID, data: ApplicationSettingsIn
) -> ApplicationSettingsIn:
    stored = session.get(ApplicationSettings, user_id) or ApplicationSettings(user_id=user_id)
    stored.mode = data.mode
    stored.auto_submit_providers = list(dict.fromkeys(data.auto_submit_providers))
    stored.max_daily_submissions = data.max_daily_submissions
    stored.assisted_daily_limits = dict(data.assisted_daily_limits)
    session.add(stored)
    record_audit_event(
        session,
        action="application_settings.updated",
        user_id=user_id,
        details=data.model_dump(mode="json"),
    )
    session.commit()
    return get_settings(session, user_id)


# --- Criação e consulta ---------------------------------------------------------------


def create_application(
    session: Session, user_id: uuid.UUID, job_id: uuid.UUID
) -> tuple[Application, bool]:
    """Cria a candidatura da vaga. Idempotente: se já existir, devolve a existente."""
    job_service.get_job(session, user_id, job_id)
    new_id = session.scalar(
        insert(Application)
        .values(
            id=uuid.uuid4(),
            user_id=user_id,
            job_id=job_id,
            status=ApplicationStatus.DISCOVERED,
        )
        .on_conflict_do_nothing(constraint="uq_applications_user_job")
        .returning(Application.id)
    )
    if new_id is None:
        existing = session.scalar(
            select(Application).where(Application.user_id == user_id, Application.job_id == job_id)
        )
        if existing is None:
            raise ConflictError("Conflito ao criar a candidatura; tente novamente.")
        return existing, False

    application = session.get_one(Application, new_id)
    record_event(
        session,
        application,
        "created",
        actor=EventActor.USER,
        to_status=ApplicationStatus.DISCOVERED,
    )
    record_audit_event(
        session,
        action="application.created",
        user_id=user_id,
        entity_type="application",
        entity_id=application.id,
    )
    session.commit()
    return application, True


def get_application(
    session: Session, user_id: uuid.UUID, application_id: uuid.UUID, *, for_update: bool = False
) -> Application:
    statement = select(Application).where(
        Application.id == application_id, Application.user_id == user_id
    )
    if for_update:
        # Trava a linha até o fim da transação: duas ações simultâneas sobre a mesma
        # candidatura (ex.: dois cliques em "enviar") passam a ser executadas em fila.
        statement = statement.with_for_update()
    application = session.scalar(statement)
    if application is None:
        raise NotFoundError("Candidatura não encontrada.")
    return application


def list_applications(
    session: Session, user_id: uuid.UUID, filters: ApplicationListFilters
) -> tuple[list[Application], int]:
    conditions = [Application.user_id == user_id]
    if filters.status:
        conditions.append(Application.status == filters.status)
    total = session.scalar(select(func.count()).select_from(Application).where(*conditions))
    items = session.scalars(
        select(Application)
        .where(*conditions)
        .order_by(Application.updated_at.desc())
        .limit(filters.limit)
        .offset(filters.offset)
    )
    return list(items), total or 0


def list_events(
    session: Session, user_id: uuid.UUID, application_id: uuid.UUID
) -> list[ApplicationEvent]:
    get_application(session, user_id, application_id)
    return list(
        session.scalars(
            select(ApplicationEvent)
            .where(ApplicationEvent.application_id == application_id)
            .order_by(ApplicationEvent.sequence)
        )
    )


# --- Preparação -----------------------------------------------------------------------


def prepare_application(
    session: Session,
    analyzer: RequirementAnalyzer,
    writer: CoverLetterWriter,
    submitters: SubmitterRegistry,
    user_id: uuid.UUID,
    application_id: uuid.UUID,
    request: ApplicationPrepareRequest,
) -> Application:
    """Analisa a vaga e, se qualificada, monta a candidatura para aprovação."""
    application = get_application(session, user_id, application_id, for_update=True)
    if application.status not in PREPARABLE_STATUSES:
        raise ConflictError(f"Candidatura em {application.status} não pode ser preparada.")
    job = job_service.get_job(session, user_id, application.job_id)
    profile = _require_profile(session, user_id)
    _ensure_assisted_quota(session, user_id, job)

    transition(session, application, ApplicationStatus.ANALYZING, actor=EventActor.AGENT)
    analysis = _get_or_run_analysis(session, analyzer, application, job, profile)

    if analysis.status is AnalysisStatus.PENDING_INFORMATION:
        transition(
            session,
            application,
            ApplicationStatus.DISCOVERED,
            actor=EventActor.AGENT,
            event_type="analysis_pending",
            details={"reasons": analysis.decision_reasons},
        )
        session.commit()
        raise ValidationError("Análise pendente: a descrição da vaga é insuficiente.")

    _apply_analysis(application, analysis)
    if analysis.recommendation is not Recommendation.QUALIFIED:
        transition(
            session,
            application,
            ApplicationStatus.REJECTED,
            actor=EventActor.AGENT,
            details={"score": analysis.score, "reasons": analysis.decision_reasons},
        )
        session.commit()
        return application

    transition(
        session,
        application,
        ApplicationStatus.QUALIFIED,
        actor=EventActor.AGENT,
        details={"score": analysis.score},
    )
    application.cover_letter_id = _current_or_new_cover_letter(session, writer, user_id, job.id).id
    application.resume_id = _choose_resume(session, user_id, request.resume_id)
    application.pending_questions = _unanswered_questions(
        submitters, job, profile, application.answers
    )
    _route_after_preparation(session, submitters, application, job)
    record_audit_event(
        session,
        action="application.prepared",
        user_id=user_id,
        entity_type="application",
        entity_id=application.id,
        details={"status": application.status},
    )
    session.commit()
    return application


def _route_after_preparation(
    session: Session, submitters: SubmitterRegistry, application: Application, job: JobOpportunity
) -> None:
    settings = get_settings(session, application.user_id)
    blockers = policy.auto_submit_blockers(session, submitters, application, job, settings)
    if not blockers:
        transition(
            session,
            application,
            ApplicationStatus.READY_TO_APPLY,
            actor=EventActor.AGENT,
            event_type="auto_approved",
            details={"rule": "automatic_mode"},
        )
        return
    transition(
        session,
        application,
        ApplicationStatus.AWAITING_APPROVAL,
        actor=EventActor.AGENT,
        details={"approval_required_because": blockers},
    )
    notification_service.notify(
        session,
        application.user_id,
        NotificationEvent.APPROVAL_PENDING,
        title="Candidatura aguardando sua aprovação",
        body=f"{job.title} — {job.company} (nota {application.compatibility_score})",
        data={"application_id": str(application.id)},
        dedup_key=f"approval_pending:{application.id}",
    )


def _ensure_assisted_quota(session: Session, user_id: uuid.UUID, job: JobOpportunity) -> None:
    quota = policy.assisted_quota(
        session, user_id, job.provider_key, get_settings(session, user_id)
    )
    if quota is not None and quota.remaining == 0:
        raise ValidationError(
            f"Limite diário de {quota.limit} candidaturas em {quota.platform} atingido "
            f"({quota.applied_today} enviadas hoje, {quota.pending_kits} aguardando envio)."
        )


# --- Kit do modo assistido -------------------------------------------------------------

KIT_STATUSES = {ApplicationStatus.AWAITING_APPROVAL, ApplicationStatus.READY_TO_APPLY}


def build_kit(session: Session, user_id: uuid.UUID, application_id: uuid.UUID) -> dict:
    """Monta o kit de candidatura: link, texto, currículo e respostas do perfil."""
    application = get_application(session, user_id, application_id)
    if application.status not in KIT_STATUSES:
        raise ConflictError(
            "O kit fica disponível depois de preparar a candidatura e antes de enviá-la."
        )
    job = job_service.get_job(session, user_id, application.job_id)
    profile = _require_profile(session, user_id)
    letter = (
        session.get(CoverLetter, application.cover_letter_id)
        if application.cover_letter_id
        else None
    )
    resume = session.get(Resume, application.resume_id) if application.resume_id else None
    quota = policy.assisted_quota(
        session, user_id, job.provider_key, get_settings(session, user_id)
    )
    return {
        "application_id": application.id,
        "platform": job.provider_key,
        "job_title": job.title,
        "company": job.company,
        "job_url": job.url,
        "cover_letter": letter.content if letter else None,
        "cover_letter_warnings": letter.validation_warnings if letter else [],
        "resume_id": resume.id if resume else None,
        "resume_filename": resume.original_filename if resume else None,
        "resume_download_path": f"/api/v1/profile/resumes/{resume.id}/file" if resume else None,
        "answers": _profile_answers(profile)
        + [
            {"field": key, "label": key, "value": value}
            for key, value in application.answers.items()
        ],
        "pending_questions": application.pending_questions,
        "quota": ({**asdict(quota), "remaining": quota.remaining} if quota is not None else None),
        "next_step": (
            "Abra o link, preencha o formulário com estes dados, envie e registre em "
            f"POST /api/v1/applications/{application.id}/mark-applied."
        ),
    }


def _profile_answers(profile: ProfessionalProfile) -> list[dict[str, str]]:
    """Respostas comuns de formulários, só com o que está cadastrado no perfil."""
    location = ", ".join(part for part in (profile.city, profile.state, profile.country) if part)
    salary = policy.answer_from_profile("salary", profile)
    languages = "; ".join(
        f"{language.name} ({language.proficiency})" for language in profile.languages
    )
    candidates = [
        ("full_name", "Nome completo", profile.full_name),
        ("email", "E-mail", profile.contact_email),
        ("phone", "Telefone", profile.phone),
        ("location", "Localização", location),
        ("current_title", "Cargo atual", profile.current_title),
        ("linkedin_url", "LinkedIn", profile.linkedin_url),
        ("github_url", "GitHub", profile.github_url),
        ("portfolio_url", "Portfólio", profile.portfolio_url),
        ("salary_expectation", "Pretensão salarial", salary),
        ("availability", "Disponibilidade", profile.availability),
        ("languages", "Idiomas", languages),
    ]
    return [
        {"field": field, "label": label, "value": value}
        for field, label, value in candidates
        if value
    ]


# --- Decisões do usuário --------------------------------------------------------------


def answer_questions(
    session: Session, user_id: uuid.UUID, application_id: uuid.UUID, answers: dict[str, str]
) -> Application:
    application = get_application(session, user_id, application_id, for_update=True)
    pending_ids = {question["id"] for question in application.pending_questions}
    unknown = set(answers) - pending_ids
    if unknown:
        raise ValidationError(f"Perguntas inexistentes ou já respondidas: {sorted(unknown)}.")

    application.answers = {**application.answers, **answers}
    application.pending_questions = [
        question for question in application.pending_questions if question["id"] not in answers
    ]
    # Registramos quais perguntas foram respondidas, nunca o conteúdo das respostas.
    record_event(
        session,
        application,
        "questions_answered",
        actor=EventActor.USER,
        details={"question_ids": sorted(answers)},
    )
    session.commit()
    return application


def approve_application(
    session: Session,
    submitters: SubmitterRegistry,
    user_id: uuid.UUID,
    application_id: uuid.UUID,
    request: ApplicationApproveRequest,
) -> Application:
    application = get_application(session, user_id, application_id, for_update=True)
    if application.status is not ApplicationStatus.AWAITING_APPROVAL:
        raise ConflictError("Só candidaturas aguardando aprovação podem ser aprovadas.")
    if application.pending_questions:
        labels = [question["label"] for question in application.pending_questions]
        raise ValidationError(f"Responda antes as perguntas pendentes: {'; '.join(labels)}.")

    letter = _get_job_cover_letter(
        session, user_id, application.job_id, request.cover_letter_id or application.cover_letter_id
    )
    if letter.validation_warnings and not request.acknowledge_warnings:
        raise ValidationError(
            "O texto tem alertas de validação. Revise-o e confirme com acknowledge_warnings=true."
        )
    resume_id = request.resume_id or application.resume_id
    if resume_id is not None:
        _get_resume(session, user_id, resume_id)
    job = job_service.get_job(session, user_id, application.job_id)
    submitter = submitters.get(job.provider_key)
    if submitter and submitter.requires_resume and resume_id is None:
        raise ValidationError("Selecione um currículo (resume_id) para esta candidatura.")

    application.cover_letter_id = letter.id
    application.resume_id = resume_id
    session.add(
        ApplicationApproval(
            application_id=application.id,
            decision=ApprovalDecision.APPROVED,
            decided_by=user_id,
            cover_letter_id=letter.id,
            resume_id=resume_id,
            acknowledged_warnings=request.acknowledge_warnings,
            comment=request.comment,
        )
    )
    transition(
        session,
        application,
        ApplicationStatus.READY_TO_APPLY,
        actor=EventActor.USER,
        event_type="approved",
        details={"cover_letter_version": letter.version},
    )
    record_audit_event(
        session,
        action="application.approved",
        user_id=user_id,
        entity_type="application",
        entity_id=application.id,
    )
    session.commit()
    return application


def decline_application(
    session: Session, user_id: uuid.UUID, application_id: uuid.UUID, reason: str
) -> Application:
    """O usuário desiste da vaga: a candidatura é encerrada sem envio."""
    application = get_application(session, user_id, application_id, for_update=True)
    if application.status not in DECLINABLE_STATUSES:
        raise ConflictError(f"Candidatura em {application.status} não pode ser recusada.")
    session.add(
        ApplicationApproval(
            application_id=application.id,
            decision=ApprovalDecision.REJECTED,
            decided_by=user_id,
            comment=reason,
        )
    )
    transition(
        session,
        application,
        ApplicationStatus.CLOSED,
        actor=EventActor.USER,
        event_type="declined",
        details={"reason": reason},
    )
    record_audit_event(
        session,
        action="application.declined",
        user_id=user_id,
        entity_type="application",
        entity_id=application.id,
    )
    session.commit()
    return application


def mark_applied_manually(
    session: Session,
    user_id: uuid.UUID,
    application_id: uuid.UUID,
    confirmation: str,
    applied_at: datetime | None,
) -> Application:
    """Registra uma candidatura feita pelo usuário diretamente na plataforma."""
    application = get_application(session, user_id, application_id, for_update=True)
    if application.status not in MANUALLY_APPLICABLE_STATUSES:
        raise ConflictError(
            f"Candidatura em {application.status} não pode ser marcada como enviada."
        )
    application.submission_channel = MANUAL_CHANNEL
    application.confirmation_source = ConfirmationSource.USER_DECLARED
    application.external_confirmation_id = confirmation
    application.submitted_at = applied_at or datetime.now(UTC)
    application.last_error = None
    transition(
        session,
        application,
        ApplicationStatus.APPLIED,
        actor=EventActor.USER,
        event_type="applied_manually",
        details={"confirmation_source": ConfirmationSource.USER_DECLARED},
    )
    record_audit_event(
        session,
        action="application.marked_applied",
        user_id=user_id,
        entity_type="application",
        entity_id=application.id,
    )
    session.commit()
    return application


def update_progress(
    session: Session,
    user_id: uuid.UUID,
    application_id: uuid.UUID,
    target: ApplicationStatus,
    note: str | None,
) -> Application:
    if target not in PROGRESS_STATUSES:
        raise ValidationError("Status inválido para acompanhamento do processo seletivo.")
    application = get_application(session, user_id, application_id, for_update=True)
    transition(
        session,
        application,
        target,
        actor=EventActor.USER,
        event_type="progress_updated",
        details={"note": note} if note else None,
    )
    session.commit()
    return application


# --- Auxiliares -----------------------------------------------------------------------


def _require_profile(session: Session, user_id: uuid.UUID) -> ProfessionalProfile:
    try:
        return profile_service.get_profile(session, user_id)
    except NotFoundError as exc:
        raise ValidationError("Cadastre seu perfil profissional antes de se candidatar.") from exc


def _get_or_run_analysis(
    session: Session,
    analyzer: RequirementAnalyzer,
    application: Application,
    job: JobOpportunity,
    profile: ProfessionalProfile,
) -> JobAnalysis:
    """Reaproveita a última análise se perfil e preferências não mudaram; senão, refaz."""
    latest = analysis_service.get_latest_analysis(session, application.user_id, job.id)
    preferences = analysis_service.get_preferences(session, application.user_id)
    is_current = latest is not None and (
        latest.profile_version == profile.updated_at.isoformat()
        and latest.min_score == preferences.min_score
        and latest.weights == preferences.weights.model_dump()
    )
    if is_current:
        return latest
    try:
        return analysis_service.analyze_job(session, analyzer, application.user_id, job.id)
    except IntegrationError:
        # A análise falhou (ex.: IA indisponível): a candidatura volta a DISCOVERED
        # para poder ser preparada de novo depois.
        session.refresh(application)
        transition(
            session,
            application,
            ApplicationStatus.DISCOVERED,
            actor=EventActor.SYSTEM,
            event_type="analysis_failed",
        )
        session.commit()
        raise


def _apply_analysis(application: Application, analysis: JobAnalysis) -> None:
    met = [
        item
        for item in analysis.requirements
        if item["status"] in {RequirementStatus.MET, RequirementStatus.PARTIALLY_MET}
    ]
    # Destaques do currículo: primeiro o que comprova requisitos obrigatórios.
    ordered = sorted(met, key=lambda item: not item["mandatory"])
    highlights = [label for item in ordered for label in item.get("evidence", [])]
    application.analysis_id = analysis.id
    application.compatibility_score = analysis.score
    application.compatibility_summary = " ".join(analysis.decision_reasons) or None
    application.met_requirements = [item["requirement"] for item in met]
    application.resume_highlights = list(dict.fromkeys(highlights))[:MAX_HIGHLIGHTS]


def _current_or_new_cover_letter(
    session: Session, writer: CoverLetterWriter, user_id: uuid.UUID, job_id: uuid.UUID
) -> CoverLetter:
    letters = cover_letter_service.list_cover_letters(session, user_id, job_id)
    if letters:
        return letters[0]
    return cover_letter_service.generate_cover_letter(
        session, writer, user_id, job_id, CoverLetterGenerateRequest()
    )


def _get_job_cover_letter(
    session: Session, user_id: uuid.UUID, job_id: uuid.UUID, letter_id: uuid.UUID | None
) -> CoverLetter:
    if letter_id is None:
        raise ValidationError("A candidatura não tem texto de apresentação.")
    letter = cover_letter_service.get_cover_letter(session, user_id, letter_id)
    if letter.job_id != job_id:
        raise ValidationError("O texto de apresentação pertence a outra vaga.")
    return letter


def _choose_resume(
    session: Session, user_id: uuid.UUID, resume_id: uuid.UUID | None
) -> uuid.UUID | None:
    if resume_id is not None:
        return _get_resume(session, user_id, resume_id).id
    return session.scalar(
        select(Resume.id)
        .where(Resume.user_id == user_id, Resume.import_status != ResumeImportStatus.FAILED)
        .order_by(Resume.created_at.desc())
        .limit(1)
    )


def _get_resume(session: Session, user_id: uuid.UUID, resume_id: uuid.UUID) -> Resume:
    resume = session.scalar(select(Resume).where(Resume.id == resume_id, Resume.user_id == user_id))
    if resume is None:
        raise NotFoundError("Currículo não encontrado.")
    return resume


def _unanswered_questions(
    submitters: SubmitterRegistry,
    job: JobOpportunity,
    profile: ProfessionalProfile,
    answers: dict[str, str],
) -> list[dict[str, str]]:
    """Perguntas obrigatórias sem resposta no perfil nem dada pelo usuário.

    Nunca inventamos respostas: o que não está cadastrado vira intervenção humana.
    """
    submitter = submitters.get(job.provider_key)
    if submitter is None:
        return []
    pending = []
    for question in submitter.required_questions(job.external_id):
        if question.id in answers:
            continue
        if policy.answer_from_profile(question.profile_field, profile) is None:
            pending.append({"id": question.id, "label": question.label})
    return pending
