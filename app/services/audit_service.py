import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import redact
from app.models.audit import AuditLog


def record_audit_event(
    session: Session,
    *,
    action: str,
    user_id: uuid.UUID | None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Adiciona um evento de auditoria à transação corrente (quem chama faz o commit).

    Gravar na mesma transação garante que ação e auditoria persistem juntas.
    """
    session.add(
        AuditLog(
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id else None,
            details=redact(details or {}),
        )
    )
