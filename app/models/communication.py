import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import EMPTY_ARRAY, text_enum
from app.models.enums import (
    DeliveryStatus,
    EmailAccountStatus,
    EmailProvider,
    MatchStatus,
    MessageCategory,
    NotificationChannel,
    NotificationEvent,
)


class EmailAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Conta de e-mail autorizada pelo usuário. Tokens só são guardados cifrados."""

    __tablename__ = "email_accounts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[EmailProvider] = mapped_column(text_enum(EmailProvider), nullable=False)
    email_address: Mapped[str] = mapped_column(String(320), nullable=False)
    encrypted_access_token: Mapped[str | None] = mapped_column(Text)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(String(200)), default=list, server_default=EMPTY_ARRAY
    )
    status: Mapped[EmailAccountStatus] = mapped_column(
        text_enum(EmailAccountStatus), nullable=False
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        UniqueConstraint("user_id", "provider", "email_address", name="uq_email_accounts_identity"),
    )


class OAuthState(UUIDPrimaryKeyMixin, Base):
    """Estado temporário de um fluxo OAuth (proteção CSRF via `state` + PKCE).

    Guardamos só o hash do `state` e o code_verifier cifrado; cada estado vale
    uma vez e expira em minutos.
    """

    __tablename__ = "oauth_states"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[EmailProvider] = mapped_column(text_enum(EmailProvider), nullable=False)
    state_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    encrypted_code_verifier: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RecruitmentMessage(UUIDPrimaryKeyMixin, Base):
    """Referência e metadados de um e-mail. O corpo da mensagem NÃO é armazenado."""

    __tablename__ = "recruitment_messages"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    email_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("email_accounts.id", ondelete="CASCADE"), nullable=False
    )
    provider_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    thread_id: Mapped[str | None] = mapped_column(String(255))
    from_address: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    snippet: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    category: Mapped[MessageCategory] = mapped_column(text_enum(MessageCategory), nullable=False)
    requires_response: Mapped[bool] = mapped_column(default=False, nullable=False)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detected_dates: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    application_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("applications.id", ondelete="SET NULL")
    )
    match_status: Mapped[MatchStatus] = mapped_column(text_enum(MatchStatus), nullable=False)
    match_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "email_account_id", "provider_message_id", name="uq_recruitment_messages_provider_id"
        ),
        Index("ix_recruitment_messages_user_received", "user_id", "received_at"),
        Index("ix_recruitment_messages_deadline", "deadline_at"),
    )


class NotificationPreferences(TimestampMixin, Base):
    __tablename__ = "notification_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    channels: Mapped[list[str]] = mapped_column(
        ARRAY(String(20)), default=list, server_default=EMPTY_ARRAY
    )
    disabled_events: Mapped[list[str]] = mapped_column(
        ARRAY(String(50)), default=list, server_default=EMPTY_ARRAY
    )
    # Intervalo mínimo entre notificações externas do mesmo tipo (controle de frequência).
    min_interval_minutes: Mapped[dict[str, int]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    email_to: Mapped[str | None] = mapped_column(String(320))
    telegram_chat_id: Mapped[str | None] = mapped_column(String(64))
    webhook_url: Mapped[str | None] = mapped_column(String(500))
    encrypted_webhook_secret: Mapped[str | None] = mapped_column(Text)


class Notification(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "notifications"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[NotificationEvent] = mapped_column(
        text_enum(NotificationEvent), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(String(2000), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # Chave de deduplicação: o mesmo fato nunca gera duas notificações.
    dedup_key: Mapped[str | None] = mapped_column(String(200))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("user_id", "dedup_key", name="uq_notifications_user_dedup"),
        Index("ix_notifications_user_created", "user_id", "created_at"),
    )


class NotificationDelivery(UUIDPrimaryKeyMixin, Base):
    """Entrega de uma notificação por um canal externo (processada pelo worker)."""

    __tablename__ = "notification_deliveries"

    notification_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("notifications.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[NotificationChannel] = mapped_column(
        text_enum(NotificationChannel), nullable=False
    )
    status: Mapped[DeliveryStatus] = mapped_column(text_enum(DeliveryStatus), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(500))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Próxima tentativa permitida (espera exponencial após falhas); nulo = imediata.
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("notification_id", "channel", name="uq_deliveries_notification_channel"),
        Index("ix_notification_deliveries_status", "status"),
    )
