"""Direitos do titular (LGPD): acesso/portabilidade (exportação) e eliminação.

A exportação reúne todos os dados ligados ao usuário, sem senhas, tokens,
segredos ou chaves internas. A exclusão apaga a conta e, em cascata, todos os
dados dela; os registros de auditoria são mantidos, mas anonimizados.
"""

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, inspect, select
from sqlalchemy.orm import Session

from app.core.errors import AuthenticationError
from app.core.security import verify_password
from app.models.analysis import AIPreferences, CoverLetter, JobAnalysis
from app.models.application import (
    Application,
    ApplicationApproval,
    ApplicationEvent,
    ApplicationSettings,
)
from app.models.audit import AuditLog
from app.models.automation import AutomationRule, ExecutionLog, ScheduledJob
from app.models.communication import (
    EmailAccount,
    Notification,
    NotificationDelivery,
    NotificationPreferences,
    RecruitmentMessage,
)
from app.models.job import JobOpportunity, JobSource
from app.models.profile import (
    Certification,
    Education,
    Language,
    ProfessionalProfile,
    Resume,
    Skill,
    WorkExperience,
)
from app.models.user import User
from app.services.audit_service import record_audit_event
from app.services.file_storage import LocalFileStorage

logger = logging.getLogger(__name__)

EXPORT_FORMAT_VERSION = 1
# Nunca exportados: credenciais, segredos e identificadores internos de armazenamento.
# Recebe o id do usuário e devolve a consulta dos registros dele em uma tabela.
QueryBuilder = Callable[[uuid.UUID], Select]

EXCLUDED_COLUMNS = frozenset(
    {
        "password_hash",
        "encrypted_access_token",
        "encrypted_refresh_token",
        "encrypted_webhook_secret",
        "storage_key",
        "submission_key",
        "row_version",
    }
)


def _profile_child(model: type) -> QueryBuilder:
    return lambda user_id: (
        select(model)
        .join(ProfessionalProfile, ProfessionalProfile.id == model.profile_id)
        .where(ProfessionalProfile.user_id == user_id)
    )


def _owned(model: type) -> QueryBuilder:
    return lambda user_id: select(model).where(model.user_id == user_id)


def _via_application(model: type) -> QueryBuilder:
    return lambda user_id: (
        select(model)
        .join(Application, Application.id == model.application_id)
        .where(Application.user_id == user_id)
    )


EXPORT_SECTIONS: dict[str, QueryBuilder] = {
    "profile": _owned(ProfessionalProfile),
    "work_experiences": _profile_child(WorkExperience),
    "educations": _profile_child(Education),
    "skills": _profile_child(Skill),
    "languages": _profile_child(Language),
    "certifications": _profile_child(Certification),
    "resumes": _owned(Resume),
    "job_sources": _owned(JobSource),
    "jobs": _owned(JobOpportunity),
    "job_analyses": _owned(JobAnalysis),
    "cover_letters": _owned(CoverLetter),
    "ai_preferences": _owned(AIPreferences),
    "application_settings": _owned(ApplicationSettings),
    "applications": _owned(Application),
    "application_events": _via_application(ApplicationEvent),
    "application_approvals": _via_application(ApplicationApproval),
    "automation_rules": _owned(AutomationRule),
    "scheduled_jobs": _owned(ScheduledJob),
    "execution_logs": _owned(ExecutionLog),
    "email_accounts": _owned(EmailAccount),
    "recruitment_messages": _owned(RecruitmentMessage),
    "notification_preferences": _owned(NotificationPreferences),
    "notifications": _owned(Notification),
    "notification_deliveries": lambda user_id: (
        select(NotificationDelivery)
        .join(Notification, Notification.id == NotificationDelivery.notification_id)
        .where(Notification.user_id == user_id)
    ),
    "audit_logs": _owned(AuditLog),
}


def export_user_data(session: Session, user: User) -> dict[str, Any]:
    data: dict[str, Any] = {
        "format_version": EXPORT_FORMAT_VERSION,
        "exported_at": datetime.now(UTC),
        "account": _serialize(user),
        "notes": [
            "Os arquivos PDF dos currículos podem ser baixados em "
            "GET /api/v1/profile/resumes/{id}/file.",
            "Senhas, tokens de integração e segredos não são exportados por segurança.",
            "Os e-mails monitorados aparecem só com metadados: o corpo nunca foi armazenado.",
        ],
    }
    for section, build_query in EXPORT_SECTIONS.items():
        data[section] = [_serialize(row) for row in session.scalars(build_query(user.id))]
    record_audit_event(session, action="privacy.data_exported", user_id=user.id)
    session.commit()
    return data


def delete_account(session: Session, storage: LocalFileStorage, user: User, password: str) -> None:
    """Apaga a conta e todos os dados dela (cascata no banco) e os arquivos em disco."""
    if not verify_password(password, user.password_hash):
        raise AuthenticationError("Senha incorreta.")

    user_id = user.id
    resume_keys = list(session.scalars(select(Resume.storage_key).where(Resume.user_id == user_id)))
    session.delete(user)
    # A auditoria guarda que uma conta foi excluída, sem dados que identifiquem a pessoa.
    # Os registros anteriores ficam com user_id nulo (ON DELETE SET NULL).
    record_audit_event(
        session,
        action="privacy.account_deleted",
        user_id=None,
        details={"removed_resume_files": len(resume_keys)},
    )
    session.commit()
    _delete_files(storage, resume_keys, user_id)


def _delete_files(storage: LocalFileStorage, keys: list[str], user_id: uuid.UUID) -> None:
    for key in keys:
        try:
            storage.delete(key)
        except (OSError, ValueError):
            # O banco já foi apagado; registramos para limpeza manual do arquivo.
            logger.exception("privacy_file_delete_failed", extra={"storage_key": key})


def _serialize(row: Any) -> dict[str, Any]:
    return {
        attribute.key: getattr(row, attribute.key)
        for attribute in inspect(row).mapper.column_attrs
        if attribute.key not in EXCLUDED_COLUMNS
    }
