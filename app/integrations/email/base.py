"""Contrato dos conectores de e-mail (somente leitura)."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from app.models.enums import EmailProvider


class EmailProviderError(Exception):
    """Falha ao falar com o provedor de e-mail (rede, token inválido, permissão)."""


@dataclass(frozen=True)
class OAuthTokens:
    access_token: str
    refresh_token: str | None
    expires_at: datetime | None
    scopes: list[str]


@dataclass(frozen=True)
class EmailMessage:
    """Somente os metadados necessários; o corpo do e-mail nunca é baixado."""

    provider_message_id: str
    thread_id: str | None
    from_address: str
    subject: str
    snippet: str
    received_at: datetime


@dataclass(frozen=True)
class MessageBatch:
    """Mensagens novas de uma sincronização.

    `has_more` indica que o limite foi atingido e ainda há mensagens no período:
    nesse caso o cursor da conta não avança, e a próxima sincronização continua.
    """

    messages: list[EmailMessage]
    has_more: bool


class EmailClient(ABC):
    provider: EmailProvider
    uses_oauth: bool = True

    def authorization_url(self, *, state: str, code_challenge: str, redirect_uri: str) -> str:
        raise NotImplementedError

    def exchange_code(self, *, code: str, code_verifier: str, redirect_uri: str) -> OAuthTokens:
        raise NotImplementedError

    def refresh(self, refresh_token: str) -> OAuthTokens:
        raise NotImplementedError

    @abstractmethod
    def account_email(self, access_token: str) -> str: ...

    @abstractmethod
    def list_messages(
        self, access_token: str, since: datetime, limit: int, known_ids: set[str]
    ) -> MessageBatch:
        """Mensagens recebidas desde `since`, ignorando as já conhecidas (`known_ids`)."""
