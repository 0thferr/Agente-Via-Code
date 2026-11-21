"""Upload de currículos em PDF e importação assistida para o perfil.

Fluxo: upload -> extração de texto -> rascunho (pending_review) -> o usuário
revisa/edita -> confirmação -> perfil salvo. Nada extraído vai direto ao perfil.
"""

import hashlib
import io
import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import PurePath
from typing import Any

from pypdf import PdfReader
from pypdf.errors import PdfReadError, PyPdfError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.llm import AIProviderError
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models.enums import ResumeImportStatus
from app.models.profile import Resume
from app.schemas.profile import ProfileIn
from app.services import profile_service
from app.services.audit_service import record_audit_event
from app.services.file_storage import LocalFileStorage
from app.services.resumes.parsing import (
    EMAIL_PATTERN,
    GITHUB_PATTERN,
    LINKEDIN_PATTERN,
    PHONE_PATTERN,
    ResumeParser,
    build_structured_draft,
)

logger = logging.getLogger(__name__)

PDF_MAGIC_BYTES = b"%PDF-"
MAX_FILENAME_LENGTH = 255
MAX_EXTRACTED_TEXT_LENGTH = 50_000
MAX_PDF_PAGES = 20


def upload_resume(
    session: Session,
    storage: LocalFileStorage,
    user_id: uuid.UUID,
    filename: str,
    content: bytes,
    parser: ResumeParser,
) -> Resume:
    if not content.startswith(PDF_MAGIC_BYTES):
        raise ValidationError("O arquivo enviado não é um PDF válido.")

    sha256 = hashlib.sha256(content).hexdigest()
    duplicate = session.scalar(
        select(Resume).where(Resume.user_id == user_id, Resume.sha256 == sha256)
    )
    if duplicate:
        raise ConflictError("Este currículo já foi enviado.")

    resume = Resume(
        user_id=user_id,
        original_filename=_safe_filename(filename),
        size_bytes=len(content),
        sha256=sha256,
        import_status=ResumeImportStatus.PENDING_REVIEW,
    )
    try:
        text = extract_pdf_text(content)
        resume.extracted_data = build_profile_draft(text, parser)
    except (PyPdfError, ValueError) as exc:
        # PDFs malformados podem gerar erros variados; o arquivo é guardado mesmo assim.
        logger.warning("resume_extraction_failed", extra={"error_type": type(exc).__name__})
        resume.import_status = ResumeImportStatus.FAILED
        resume.extraction_error = "Não foi possível ler o conteúdo do PDF."

    resume.storage_key = storage.save(content, "pdf")
    try:
        session.add(resume)
        session.flush()
        record_audit_event(
            session,
            action="resume.uploaded",
            user_id=user_id,
            entity_type="resume",
            entity_id=resume.id,
        )
        session.commit()
    except Exception:
        session.rollback()
        storage.delete(resume.storage_key)
        raise
    return resume


def extract_pdf_text(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content))
    if reader.is_encrypted:
        raise PdfReadError("PDF protegido por senha.")
    pages = reader.pages[:MAX_PDF_PAGES]
    text = "\n".join(page.extract_text() or "" for page in pages)
    return text[:MAX_EXTRACTED_TEXT_LENGTH].strip()


def build_profile_draft(text: str, parser: ResumeParser) -> dict[str, Any]:
    """Monta o rascunho do perfil para revisão.

    Contatos são extraídos localmente por regras; o restante é estruturado pelo
    leitor (simulado ou IA) e conferido contra o texto original. Nada vai para o
    perfil sem a confirmação do usuário.
    """
    notes: list[str] = []
    if not text:
        notes.append("Nenhum texto foi extraído; o PDF pode ser uma imagem digitalizada.")

    detected = {
        "contact_email": _first_match(EMAIL_PATTERN, text),
        "phone": _first_match(PHONE_PATTERN, text),
        "linkedin_url": _as_url(_first_match(LINKEDIN_PATTERN, text)),
        "github_url": _as_url(_first_match(GITHUB_PATTERN, text)),
    }
    structured: dict[str, Any] = {}
    if text:
        try:
            draft = build_structured_draft(parser.parse(text), text)
            structured = {key: value for key, value in draft.profile.items() if value}
            notes.extend(draft.warnings)
            if not structured.get("experiences"):
                notes.append("Nenhuma experiência estruturada: preencha na revisão.")
        except AIProviderError as exc:
            logger.warning("resume_structuring_failed", extra={"parser": parser.provider_name})
            notes.append(f"Estruturação automática indisponível ({exc}). Preencha na revisão.")
    contacts = {key: value for key, value in detected.items() if value}
    return {
        "profile_draft": {**structured, **contacts},
        "raw_text": text,
        "notes": notes,
        "parser": {"provider": parser.provider_name, "model": parser.model_name},
    }


def list_resumes(session: Session, user_id: uuid.UUID) -> list[Resume]:
    return list(
        session.scalars(
            select(Resume).where(Resume.user_id == user_id).order_by(Resume.created_at.desc())
        )
    )


def get_resume(session: Session, user_id: uuid.UUID, resume_id: uuid.UUID) -> Resume:
    # Filtrar por user_id garante que um usuário nunca acesse o currículo de outro;
    # respondemos 404 (e não 403) para não revelar que o recurso existe.
    resume = session.scalar(select(Resume).where(Resume.id == resume_id, Resume.user_id == user_id))
    if resume is None:
        raise NotFoundError("Currículo não encontrado.")
    return resume


def confirm_import(
    session: Session, user_id: uuid.UUID, resume_id: uuid.UUID, reviewed_profile: ProfileIn
) -> Resume:
    resume = get_resume(session, user_id, resume_id)
    if resume.import_status is not ResumeImportStatus.PENDING_REVIEW:
        raise ConflictError("Este currículo não está aguardando revisão.")

    resume.import_status = ResumeImportStatus.CONFIRMED
    resume.confirmed_at = datetime.now(UTC)
    profile_service.save_profile(session, user_id, reviewed_profile, source="resume_import")
    return resume


def _first_match(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    return match.group(0).strip() if match else None


def _as_url(value: str | None) -> str | None:
    if value is None:
        return None
    return value if value.lower().startswith("http") else f"https://{value}"


def _safe_filename(filename: str) -> str:
    name = PurePath(filename.replace("\\", "/")).name or "curriculo.pdf"
    return name[:MAX_FILENAME_LENGTH]
