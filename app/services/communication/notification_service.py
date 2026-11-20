"""Notificações: criação deduplicada, preferências, frequência e entrega pelo worker.

`notify` só grava no banco (na transação de quem chama), sem fazer chamadas de
rede. As entregas externas ficam pendentes e o worker as envia com retentativas.
Assim, uma API lenta do Telegram nunca atrasa uma requisição do usuário.
"""

import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.crypto import SecretCipher
from app.core.errors import NotFoundError
from app.integrations.notifications.channels import (
    ChannelRegistry,
    DeliveryError,
    Destination,
    OutgoingNotification,
)
from app.models.communication import Notification, NotificationDelivery, NotificationPreferences
from app.models.enums import DeliveryStatus, NotificationChannel, NotificationEvent
from app.models.user import User
from app.schemas.communication import (
    NotificationFilters,
    NotificationPreferencesIn,
    NotificationPreferencesOut,
)
from app.services.audit_service import record_audit_event

logger = logging.getLogger(__name__)

MAX_DELIVERY_ATTEMPTS = 3
DELIVERY_BATCH_SIZE = 50
WEBHOOK_SECRET_BYTES = 32


# --- Criação --------------------------------------------------------------------------


def notify(
    session: Session,
    user_id: uuid.UUID,
    event: NotificationEvent,
    *,
    title: str,
    body: str,
    data: dict[str, Any] | None = None,
    dedup_key: str | None = None,
    now: datetime | None = None,
) -> Notification | None:
    """Registra a notificação e agenda as entregas. Não faz commit."""
    now = now or datetime.now(UTC)
    preferences = session.get(NotificationPreferences, user_id)
    if preferences and event.value in preferences.disabled_events:
        return None

    notification_id = session.scalar(
        insert(Notification)
        .values(
            id=uuid.uuid4(),
            user_id=user_id,
            event_type=event,
            title=title[:200],
            body=body[:2000],
            data=data or {},
            dedup_key=dedup_key,
        )
        .on_conflict_do_nothing(constraint="uq_notifications_user_dedup")
        .returning(Notification.id)
    )
    if notification_id is None:
        return None  # o mesmo fato já foi notificado

    for channel in preferences.channels if preferences else []:
        status = DeliveryStatus.PENDING
        if _too_frequent(session, user_id, event, NotificationChannel(channel), preferences, now):
            status = DeliveryStatus.SKIPPED
        session.add(
            NotificationDelivery(
                notification_id=notification_id,
                channel=NotificationChannel(channel),
                status=status,
                last_error="Controle de frequência." if status is DeliveryStatus.SKIPPED else None,
            )
        )
    return session.get(Notification, notification_id)


def _too_frequent(
    session: Session,
    user_id: uuid.UUID,
    event: NotificationEvent,
    channel: NotificationChannel,
    preferences: NotificationPreferences,
    now: datetime,
) -> bool:
    minutes = preferences.min_interval_minutes.get(event.value, 0)
    if minutes <= 0:
        return False
    recent = session.scalar(
        select(func.count())
        .select_from(NotificationDelivery)
        .join(Notification, Notification.id == NotificationDelivery.notification_id)
        .where(
            Notification.user_id == user_id,
            Notification.event_type == event,
            NotificationDelivery.channel == channel,
            NotificationDelivery.status.in_([DeliveryStatus.PENDING, DeliveryStatus.SENT]),
            Notification.created_at >= now - timedelta(minutes=minutes),
        )
    )
    return bool(recent)


# --- Entrega (worker) -----------------------------------------------------------------


def deliver_pending(
    session: Session, channels: ChannelRegistry, cipher: SecretCipher, now: datetime
) -> int:
    deliveries = session.scalars(
        select(NotificationDelivery)
        .where(
            NotificationDelivery.status == DeliveryStatus.PENDING,
            NotificationDelivery.attempts < MAX_DELIVERY_ATTEMPTS,
        )
        .order_by(NotificationDelivery.id)
        .limit(DELIVERY_BATCH_SIZE)
        .with_for_update(skip_locked=True)
    ).all()
    sent = 0
    for delivery in deliveries:
        notification = session.get_one(Notification, delivery.notification_id)
        preferences = session.get(NotificationPreferences, notification.user_id)
        channel = channels.get(delivery.channel)
        delivery.attempts += 1
        destination = _destination(preferences, cipher) if preferences else None
        if channel is None or not channel.is_configured() or destination is None:
            _mark(delivery, DeliveryStatus.SKIPPED, "Canal não configurado no servidor.")
            continue
        if not channel.has_destination(destination):
            _mark(delivery, DeliveryStatus.SKIPPED, "Destino não informado nas preferências.")
            continue
        try:
            channel.send(_outgoing(notification), destination)
        except DeliveryError as exc:
            logger.warning(
                "notification_delivery_failed",
                extra={"channel": delivery.channel, "attempt": delivery.attempts},
            )
            final = delivery.attempts >= MAX_DELIVERY_ATTEMPTS
            _mark(delivery, DeliveryStatus.FAILED if final else DeliveryStatus.PENDING, str(exc))
            continue
        delivery.status = DeliveryStatus.SENT
        delivery.sent_at = now
        delivery.last_error = None
        sent += 1
    session.commit()
    return sent


def _mark(delivery: NotificationDelivery, status: DeliveryStatus, error: str) -> None:
    delivery.status = status
    delivery.last_error = error[:500]


def _outgoing(notification: Notification) -> OutgoingNotification:
    return OutgoingNotification(
        event_type=notification.event_type.value,
        title=notification.title,
        body=notification.body,
        data=notification.data,
    )


def _destination(preferences: NotificationPreferences, cipher: SecretCipher) -> Destination:
    secret = (
        cipher.decrypt(preferences.encrypted_webhook_secret)
        if preferences.encrypted_webhook_secret and cipher.is_configured
        else None
    )
    return Destination(
        email_to=preferences.email_to,
        telegram_chat_id=preferences.telegram_chat_id,
        webhook_url=preferences.webhook_url,
        webhook_secret=secret,
    )


# --- Preferências e consulta ----------------------------------------------------------


def get_preferences(session: Session, user_id: uuid.UUID) -> NotificationPreferencesOut:
    stored = session.get(NotificationPreferences, user_id)
    if stored is None:
        user = session.get_one(User, user_id)
        return NotificationPreferencesOut(email_to=user.email, webhook_secret_configured=False)
    return _preferences_out(stored)


def save_preferences(
    session: Session, cipher: SecretCipher, user_id: uuid.UUID, data: NotificationPreferencesIn
) -> NotificationPreferencesOut:
    stored = session.get(NotificationPreferences, user_id) or NotificationPreferences(
        user_id=user_id
    )
    new_secret = None
    if data.webhook_url and not stored.encrypted_webhook_secret:
        new_secret = secrets.token_urlsafe(WEBHOOK_SECRET_BYTES)
        stored.encrypted_webhook_secret = cipher.encrypt(new_secret)
    if not data.webhook_url:
        stored.encrypted_webhook_secret = None

    stored.channels = [channel.value for channel in dict.fromkeys(data.channels)]
    stored.disabled_events = [event.value for event in dict.fromkeys(data.disabled_events)]
    stored.min_interval_minutes = {
        event.value: minutes for event, minutes in data.min_interval_minutes.items()
    }
    stored.email_to = data.email_to
    stored.telegram_chat_id = data.telegram_chat_id
    stored.webhook_url = data.webhook_url
    session.add(stored)
    record_audit_event(
        session,
        action="notification_preferences.updated",
        user_id=user_id,
        details={"channels": stored.channels},
    )
    session.commit()
    result = _preferences_out(stored)
    result.webhook_secret = new_secret
    return result


def _preferences_out(stored: NotificationPreferences) -> NotificationPreferencesOut:
    return NotificationPreferencesOut(
        channels=stored.channels,
        disabled_events=stored.disabled_events,
        min_interval_minutes=stored.min_interval_minutes,
        email_to=stored.email_to,
        telegram_chat_id=stored.telegram_chat_id,
        webhook_url=stored.webhook_url,
        webhook_secret_configured=bool(stored.encrypted_webhook_secret),
    )


def list_notifications(
    session: Session, user_id: uuid.UUID, filters: NotificationFilters
) -> tuple[list[Notification], int, int]:
    conditions = [Notification.user_id == user_id]
    if filters.unread_only:
        conditions.append(Notification.read_at.is_(None))
    total = session.scalar(select(func.count()).select_from(Notification).where(*conditions))
    unread = session.scalar(
        select(func.count())
        .select_from(Notification)
        .where(Notification.user_id == user_id, Notification.read_at.is_(None))
    )
    items = session.scalars(
        select(Notification)
        .where(*conditions)
        .order_by(Notification.created_at.desc(), Notification.id)
        .limit(filters.limit)
        .offset(filters.offset)
    )
    return list(items), total or 0, unread or 0


def mark_read(session: Session, user_id: uuid.UUID, notification_id: uuid.UUID) -> Notification:
    notification = session.scalar(
        select(Notification).where(
            Notification.id == notification_id, Notification.user_id == user_id
        )
    )
    if notification is None:
        raise NotFoundError("Notificação não encontrada.")
    notification.read_at = notification.read_at or datetime.now(UTC)
    session.commit()
    return notification
