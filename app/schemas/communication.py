"""Contratos de integrações de e-mail, mensagens de recrutamento e notificações."""

import ipaddress
import uuid
from datetime import datetime
from typing import Annotated, Any
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, StringConstraints

from app.models.enums import (
    EmailAccountStatus,
    EmailProvider,
    MatchStatus,
    MessageCategory,
    NotificationChannel,
    NotificationEvent,
)
from app.schemas.job import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.schemas.profile import UrlText

BLOCKED_WEBHOOK_HOSTS = {"localhost", "metadata.google.internal"}


def _validate_webhook_url(url: str) -> str:
    """Bloqueia destinos óbvios de SSRF: só HTTPS e nada de IPs internos/locais.

    Limitação: um domínio público que resolva para IP interno (DNS rebinding) não é
    detectado aqui; em produção, filtre também na saída de rede.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https":
        raise ValueError("O webhook precisa usar HTTPS.")
    if host in BLOCKED_WEBHOOK_HOSTS or host.endswith(".local") or host.endswith(".internal"):
        raise ValueError("Host de webhook não permitido.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return url
    if not address.is_global:
        raise ValueError("Webhook não pode apontar para endereço IP interno.")
    return url


WebhookUrl = Annotated[UrlText, AfterValidator(_validate_webhook_url)]


class EmailAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider: EmailProvider
    email_address: str
    status: EmailAccountStatus
    scopes: list[str]
    last_synced_at: datetime | None
    last_error: str | None
    created_at: datetime


class ConnectResponse(BaseModel):
    authorization_url: str | None = Field(
        default=None, description="Abra esta URL no navegador para autorizar o acesso."
    )
    account: EmailAccountOut | None = Field(
        default=None, description="Preenchido quando a conexão é imediata (provedor simulado)."
    )


class EmailIntegrationStatus(BaseModel):
    provider: EmailProvider
    configured: bool = Field(description="O servidor tem as credenciais OAuth deste provedor.")
    accounts: list[EmailAccountOut]


class JobProviderStatus(BaseModel):
    key: str
    name: str
    integration_status: str


class IntegrationsOverview(BaseModel):
    job_providers: list[JobProviderStatus]
    email: list[EmailIntegrationStatus]
    notification_channels: dict[NotificationChannel, bool]


class RecruitmentMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email_account_id: uuid.UUID
    from_address: str
    subject: str
    snippet: str
    received_at: datetime
    category: MessageCategory
    requires_response: bool
    deadline_at: datetime | None
    detected_dates: list[str]
    application_id: uuid.UUID | None
    match_status: MatchStatus
    match_confidence: float


class MessageFilters(BaseModel):
    category: MessageCategory | None = None
    match_status: MatchStatus | None = None
    application_id: uuid.UUID | None = None
    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    offset: int = Field(default=0, ge=0)


class MessageListResponse(BaseModel):
    items: list[RecruitmentMessageOut]
    total: int
    limit: int
    offset: int


class MessageAssociationRequest(BaseModel):
    application_id: uuid.UUID | None = Field(
        description="Candidatura correta, ou null se a mensagem não pertence a nenhuma."
    )


class NotificationPreferencesIn(BaseModel):
    channels: list[NotificationChannel] = Field(
        default_factory=list,
        description="Canais externos; as notificações in-app são sempre salvas.",
    )
    disabled_events: list[NotificationEvent] = Field(default_factory=list)
    min_interval_minutes: dict[NotificationEvent, Annotated[int, Field(ge=0, le=7 * 24 * 60)]] = (
        Field(default_factory=dict)
    )
    email_to: EmailStr | None = None
    telegram_chat_id: Annotated[str, StringConstraints(pattern=r"^-?\d{1,20}$")] | None = None
    webhook_url: WebhookUrl | None = None


class NotificationPreferencesOut(NotificationPreferencesIn):
    webhook_secret_configured: bool
    webhook_secret: str | None = Field(
        default=None,
        description="Segredo HMAC do webhook. Só é mostrado uma vez, quando é gerado.",
    )


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_type: NotificationEvent
    title: str
    body: str
    data: dict[str, Any]
    read_at: datetime | None
    created_at: datetime


class NotificationFilters(BaseModel):
    unread_only: bool = False
    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    offset: int = Field(default=0, ge=0)


class NotificationListResponse(BaseModel):
    items: list[NotificationOut]
    total: int
    unread: int
    limit: int
    offset: int
