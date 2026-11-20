import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUserDep, SessionDep
from app.schemas.communication import (
    MessageAssociationRequest,
    MessageFilters,
    MessageListResponse,
    RecruitmentMessageOut,
)
from app.services.communication import email_service

router = APIRouter(prefix="/messages", tags=["messages"])


@router.get("", response_model=MessageListResponse)
def list_messages(
    filters: Annotated[MessageFilters, Query()], session: SessionDep, current_user: CurrentUserDep
) -> MessageListResponse:
    """E-mails de recrutamento detectados (metadados e trecho; o corpo não é guardado)."""
    items, total = email_service.list_messages(session, current_user.id, filters)
    return MessageListResponse(
        items=[RecruitmentMessageOut.model_validate(item) for item in items],
        total=total,
        limit=filters.limit,
        offset=filters.offset,
    )


@router.patch("/{message_id}/association", response_model=RecruitmentMessageOut)
def set_association(
    message_id: uuid.UUID,
    payload: MessageAssociationRequest,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> RecruitmentMessageOut:
    """Valida a qual candidatura o e-mail pertence (ou que não pertence a nenhuma)."""
    message = email_service.set_association(
        session, current_user.id, message_id, payload.application_id
    )
    return RecruitmentMessageOut.model_validate(message)
