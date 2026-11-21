import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUserDep, SessionDep
from app.core.crypto import SecretCipher
from app.schemas.communication import (
    NotificationFilters,
    NotificationListResponse,
    NotificationOut,
    NotificationPreferencesIn,
    NotificationPreferencesOut,
)
from app.services.communication import notification_service
from app.wiring import get_cipher

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=NotificationListResponse)
def list_notifications(
    filters: Annotated[NotificationFilters, Query()],
    session: SessionDep,
    current_user: CurrentUserDep,
) -> NotificationListResponse:
    items, total, unread = notification_service.list_notifications(
        session, current_user.id, filters
    )
    return NotificationListResponse(
        items=[NotificationOut.model_validate(item) for item in items],
        total=total,
        unread=unread,
        limit=filters.limit,
        offset=filters.offset,
    )


@router.get("/preferences", response_model=NotificationPreferencesOut)
def get_preferences(
    session: SessionDep, current_user: CurrentUserDep
) -> NotificationPreferencesOut:
    return notification_service.get_preferences(session, current_user.id)


@router.put("/preferences", response_model=NotificationPreferencesOut)
def put_preferences(
    payload: NotificationPreferencesIn,
    session: SessionDep,
    cipher: Annotated[SecretCipher, Depends(get_cipher)],
    current_user: CurrentUserDep,
) -> NotificationPreferencesOut:
    """Canais, eventos desativados e intervalo mínimo por evento. Ao informar um webhook
    pela primeira vez, o segredo HMAC é gerado e mostrado só nesta resposta."""
    return notification_service.save_preferences(session, cipher, current_user.id, payload)


@router.patch("/{notification_id}/read", response_model=NotificationOut)
def mark_read(
    notification_id: uuid.UUID, session: SessionDep, current_user: CurrentUserDep
) -> NotificationOut:
    notification = notification_service.mark_read(session, current_user.id, notification_id)
    return NotificationOut.model_validate(notification)
