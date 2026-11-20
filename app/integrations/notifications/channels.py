"""Canais externos de notificação: e-mail (SMTP), Telegram e webhook.

Cada canal só envia se estiver configurado no servidor (credenciais) E nas
preferências do usuário (destino). Push não está implementado: exigiria chaves
VAPID e registro de dispositivos pelo app/dashboard, que ainda não existem.
"""

import hashlib
import hmac
import json
import smtplib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from email.message import EmailMessage

import httpx

from app.models.enums import NotificationChannel


class DeliveryError(Exception):
    """O canal não conseguiu entregar a notificação."""


@dataclass(frozen=True)
class OutgoingNotification:
    event_type: str
    title: str
    body: str
    data: dict


@dataclass(frozen=True)
class Destination:
    email_to: str | None
    telegram_chat_id: str | None
    webhook_url: str | None
    webhook_secret: str | None


class Channel(ABC):
    name: NotificationChannel

    @abstractmethod
    def is_configured(self) -> bool:
        """O servidor tem o necessário (credenciais) para usar este canal?"""

    @abstractmethod
    def has_destination(self, destination: Destination) -> bool: ...

    @abstractmethod
    def send(self, notification: OutgoingNotification, destination: Destination) -> None: ...


class SmtpEmailChannel(Channel):
    name = NotificationChannel.EMAIL

    def __init__(
        self,
        *,
        host: str | None,
        port: int,
        username: str | None,
        password: str | None,
        sender: str | None,
        timeout_seconds: float,
    ) -> None:
        self._host, self._port = host, port
        self._username, self._password = username, password
        self._sender, self._timeout = sender, timeout_seconds

    def is_configured(self) -> bool:
        return bool(self._host and self._sender)

    def has_destination(self, destination: Destination) -> bool:
        return bool(destination.email_to)

    def send(self, notification: OutgoingNotification, destination: Destination) -> None:
        message = EmailMessage()
        message["From"] = self._sender
        message["To"] = destination.email_to
        message["Subject"] = f"[JobPilot] {notification.title}"
        message.set_content(notification.body)
        try:
            with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as smtp:
                smtp.starttls()
                if self._username and self._password:
                    smtp.login(self._username, self._password)
                smtp.send_message(message)
        except (smtplib.SMTPException, OSError) as exc:
            raise DeliveryError(f"Falha no envio por SMTP: {type(exc).__name__}") from exc


class TelegramChannel(Channel):
    name = NotificationChannel.TELEGRAM
    api_base = "https://api.telegram.org"

    def __init__(
        self,
        *,
        bot_token: str | None,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._token = bot_token
        self._http = httpx.Client(timeout=timeout_seconds, transport=transport)

    def is_configured(self) -> bool:
        return bool(self._token)

    def has_destination(self, destination: Destination) -> bool:
        return bool(destination.telegram_chat_id)

    def send(self, notification: OutgoingNotification, destination: Destination) -> None:
        try:
            response = self._http.post(
                f"{self.api_base}/bot{self._token}/sendMessage",
                json={
                    "chat_id": destination.telegram_chat_id,
                    "text": f"{notification.title}\n\n{notification.body}",
                },
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # A URL contém o token do bot: nunca a incluímos na mensagem de erro.
            raise DeliveryError(f"Telegram recusou (HTTP {exc.response.status_code}).") from exc
        except httpx.HTTPError as exc:
            raise DeliveryError("Falha de comunicação com o Telegram.") from exc


class WebhookChannel(Channel):
    """POST JSON assinado com HMAC-SHA256 no cabeçalho X-JobPilot-Signature."""

    name = NotificationChannel.WEBHOOK
    signature_header = "X-JobPilot-Signature"

    def __init__(
        self, *, timeout_seconds: float, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._http = httpx.Client(
            timeout=timeout_seconds, transport=transport, follow_redirects=False
        )

    def is_configured(self) -> bool:
        return True

    def has_destination(self, destination: Destination) -> bool:
        return bool(destination.webhook_url and destination.webhook_secret)

    def send(self, notification: OutgoingNotification, destination: Destination) -> None:
        body = json.dumps(
            {
                "event": notification.event_type,
                "title": notification.title,
                "body": notification.body,
                "data": notification.data,
            },
            ensure_ascii=False,
            default=str,
        ).encode()
        signature = hmac.new(destination.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        try:
            response = self._http.post(
                destination.webhook_url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    self.signature_header: f"sha256={signature}",
                },
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise DeliveryError(f"Webhook respondeu HTTP {exc.response.status_code}.") from exc
        except httpx.HTTPError as exc:
            raise DeliveryError("Falha de comunicação com o webhook.") from exc


class ChannelRegistry:
    def __init__(self, channels: list[Channel]) -> None:
        self._channels = {channel.name: channel for channel in channels}

    def get(self, name: NotificationChannel) -> Channel | None:
        return self._channels.get(name)

    def available(self) -> dict[NotificationChannel, bool]:
        status = {name: channel.is_configured() for name, channel in self._channels.items()}
        status[NotificationChannel.PUSH] = False
        return status
