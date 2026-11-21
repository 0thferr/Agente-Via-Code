"""Geração, versionamento e revisão de textos de apresentação."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.ai.llm import AIProviderError
from app.core.errors import ConflictError, IntegrationError, NotFoundError, ValidationError
from app.models.analysis import CoverLetter
from app.models.enums import CoverLetterSource
from app.schemas.analysis import CoverLetterGenerateRequest
from app.services import job_service, profile_service
from app.services.analysis import analysis_service
from app.services.analysis.profile_facts import build_profile_facts
from app.services.audit_service import record_audit_event
from app.services.cover_letters.claim_validation import (
    unsupported_term_warnings,
    validate_cover_letter,
)
from app.services.cover_letters.writers import CoverLetterWriter, WritingContext


def generate_cover_letter(
    session: Session,
    writer: CoverLetterWriter,
    user_id: uuid.UUID,
    job_id: uuid.UUID,
    request: CoverLetterGenerateRequest,
) -> CoverLetter:
    job = job_service.get_job(session, user_id, job_id)
    try:
        profile = profile_service.get_profile(session, user_id)
    except NotFoundError as exc:
        raise ValidationError("Cadastre seu perfil profissional antes de gerar textos.") from exc

    facts = build_profile_facts(profile)
    preferences = analysis_service.get_preferences(session, user_id)
    latest_analysis = analysis_service.get_latest_analysis(session, user_id, job_id)
    matched_refs = (
        [ref for item in latest_analysis.requirements for ref in item.get("evidence_refs", [])]
        if latest_analysis
        else []
    )
    tone = request.tone or preferences.cover_letter_tone
    context = WritingContext(
        job=job,
        facts=facts,
        matched_refs=list(dict.fromkeys(matched_refs)),
        tone=tone,
        max_characters=request.max_characters,
        writing_samples=list(preferences.writing_samples),
    )
    try:
        draft = writer.write(context)
    except AIProviderError as exc:
        raise IntegrationError(f"Não foi possível gerar o texto: {exc}") from exc

    validated = validate_cover_letter(
        draft.content, draft.claims, job, facts, request.max_characters
    )
    letter = CoverLetter(
        user_id=user_id,
        job_id=job.id,
        analysis_id=latest_analysis.id if latest_analysis else None,
        source=CoverLetterSource.GENERATED,
        content=validated.content,
        tone=tone,
        max_characters=request.max_characters,
        validation_warnings=validated.warnings,
        generator_provider=writer.provider_name,
        generator_model=writer.model_name,
    )
    return _save_new_version(session, letter, action="cover_letter.generated")


def revise_cover_letter(
    session: Session, user_id: uuid.UUID, letter_id: uuid.UUID, content: str
) -> CoverLetter:
    """Edição do usuário: cria uma nova versão e mantém a anterior no histórico."""
    original = get_cover_letter(session, user_id, letter_id)
    if len(content) > original.max_characters:
        raise ValidationError(
            f"O texto tem {len(content)} caracteres; o limite é {original.max_characters}."
        )
    job = job_service.get_job(session, user_id, original.job_id)
    facts = build_profile_facts(profile_service.get_profile(session, user_id))
    warnings = unsupported_term_warnings(content, job, facts)
    letter = CoverLetter(
        user_id=user_id,
        job_id=original.job_id,
        analysis_id=original.analysis_id,
        source=CoverLetterSource.USER_EDIT,
        content=content,
        tone=original.tone,
        max_characters=original.max_characters,
        validation_warnings=warnings,
    )
    return _save_new_version(session, letter, action="cover_letter.revised")


def list_cover_letters(
    session: Session, user_id: uuid.UUID, job_id: uuid.UUID
) -> list[CoverLetter]:
    job_service.get_job(session, user_id, job_id)
    return list(
        session.scalars(
            select(CoverLetter)
            .where(CoverLetter.job_id == job_id, CoverLetter.user_id == user_id)
            .order_by(CoverLetter.version.desc())
        )
    )


def get_cover_letter(session: Session, user_id: uuid.UUID, letter_id: uuid.UUID) -> CoverLetter:
    letter = session.scalar(
        select(CoverLetter).where(CoverLetter.id == letter_id, CoverLetter.user_id == user_id)
    )
    if letter is None:
        raise NotFoundError("Texto de apresentação não encontrado.")
    return letter


def _save_new_version(session: Session, letter: CoverLetter, *, action: str) -> CoverLetter:
    current = session.scalar(
        select(func.max(CoverLetter.version)).where(CoverLetter.job_id == letter.job_id)
    )
    letter.version = (current or 0) + 1
    try:
        session.add(letter)
        session.flush()
    except IntegrityError as exc:
        # Duas gerações simultâneas para a mesma vaga: a constraint única impede
        # versões repetidas e o cliente pode simplesmente tentar de novo.
        session.rollback()
        raise ConflictError("Outra versão foi criada ao mesmo tempo; tente novamente.") from exc

    record_audit_event(
        session,
        action=action,
        user_id=letter.user_id,
        entity_type="cover_letter",
        entity_id=letter.id,
        details={"job_id": str(letter.job_id), "version": letter.version},
    )
    session.commit()
    return letter
