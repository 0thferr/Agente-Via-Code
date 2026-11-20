import hashlib
import hmac
import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.core.crypto import EncryptionNotConfiguredError, SecretCipher
from app.core.errors import DomainError
from app.integrations.email.base import EmailProviderError
from app.integrations.email.oauth_clients import GmailClient, OutlookClient
from app.integrations.notifications.channels import (
    ChannelRegistry,
    DeliveryError,
    Destination,
    OutgoingNotification,
    TelegramChannel,
    WebhookChannel,
)
from app.models.enums import MessageCategory, NotificationChannel
from app.schemas.communication import NotificationPreferencesIn
from app.services.communication.classifier import classify, extract_dates
from app.services.communication.matcher import Candidate, match_message

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)

# --- Classificação e datas -------------------------------------------------------------


@pytest.mark.parametrize(
    ("subject", "snippet", "category"),
    [
        (
            "Convite para entrevista",
            "Podemos conversar dia 25/11 às 14h?",
            MessageCategory.INTERVIEW_INVITE,
        ),
        ("Teste técnico", "Prazo de entrega: até 22/11", MessageCategory.ASSESSMENT),
        ("Proposta de emprego", "Segue a carta oferta.", MessageCategory.OFFER),
        (
            "Retorno do processo",
            "Infelizmente optamos por outro candidato.",
            MessageCategory.REJECTION,
        ),
        ("Atualização sobre sua candidatura", "Você avançou!", MessageCategory.STAGE_CHANGE),
        ("Sobre a vaga", "Recebemos seu currículo.", MessageCategory.OTHER_RECRUITING),
        ("Promoção imperdível", "50% de desconto", MessageCategory.NOT_RECRUITING),
    ],
)
def test_classification(subject, snippet, category):
    assert classify(subject, snippet, NOW).category is category


def test_interview_requires_response_and_extracts_date_with_time():
    result = classify("Convite para entrevista", "Dia 25/11 às 14h30, pode confirmar?", NOW)

    assert result.requires_response
    assert result.detected_dates == [datetime(2026, 11, 25, 14, 30, tzinfo=UTC)]
    assert result.deadline_at is None  # data de entrevista não é prazo


def test_deadline_is_detected_only_with_deadline_words():
    result = classify("Desafio técnico", "Entregar até 22/11/2026.", NOW)

    assert result.deadline_at == datetime(2026, 11, 22, 23, 59, tzinfo=UTC)


def test_date_without_year_in_the_past_rolls_to_next_year():
    assert extract_dates("até 10/01", NOW) == [datetime(2027, 1, 10, 23, 59, tzinfo=UTC)]


def test_invalid_dates_are_ignored():
    assert extract_dates("versão 45/99 e 31/02/2026", NOW) == []


# --- Associação ------------------------------------------------------------------------

ACME = Candidate(
    "app-acme", "Acme Tech", "Backend Python Júnior", "https://jobs.acme-tech.example/1"
)
ZETA = Candidate(
    "app-zeta", "Fintech Zeta", "Backend Sênior", "https://jobs.fintech-zeta.example/2"
)


def test_clear_match_by_company_and_domain():
    result = match_message("RH <rh@acme-tech.example>", "Entrevista Acme Tech", "", [ACME, ZETA])

    assert result.application_id == "app-acme" and not result.ambiguous


def test_two_applications_at_same_company_need_review():
    other_acme = Candidate("app-acme-2", "Acme Tech", "Estágio", "https://jobs.acme-tech.example/9")

    result = match_message(
        "noreply@ats.example", "Sua candidatura na Acme Tech", "", [ACME, other_acme]
    )

    assert result.ambiguous


def test_no_signal_is_unmatched():
    result = match_message("x@outra.example", "Olá", "", [ACME, ZETA])

    assert result.application_id is None


def test_brazilian_suffix_does_not_match_every_company():
    a = Candidate("a", "Empresa A", "Dev", "https://vagas.empresa-a.com.br/1")
    result = match_message("rh@empresa-b.com.br", "Retorno", "", [a])

    assert result.application_id is None


# --- Preferências e canais -------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.example.com/x",
        "https://localhost/x",
        "https://10.0.0.5/x",
        "https://169.254.169.254/latest",
        "https://servico.internal/x",
    ],
)
def test_unsafe_webhook_urls_are_rejected(url):
    with pytest.raises(ValidationError):
        NotificationPreferencesIn(webhook_url=url)


def test_public_https_webhook_is_accepted():
    assert NotificationPreferencesIn(webhook_url="https://hooks.example.com/x").webhook_url


NOTIFICATION = OutgoingNotification("interview_invite", "Título", "Corpo", {"a": 1})


def test_webhook_payload_is_signed_with_hmac():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        captured["signature"] = request.headers[WebhookChannel.signature_header]
        return httpx.Response(200)

    channel = WebhookChannel(timeout_seconds=5, transport=httpx.MockTransport(handler))
    channel.send(NOTIFICATION, Destination(None, None, "https://hooks.example.com/x", "segredo"))

    expected = hmac.new(b"segredo", captured["body"], hashlib.sha256).hexdigest()
    assert captured["signature"] == f"sha256={expected}"
    assert json.loads(captured["body"])["event"] == "interview_invite"


def test_telegram_error_never_exposes_bot_token():
    channel = TelegramChannel(
        bot_token="123:SECRET",
        timeout_seconds=5,
        transport=httpx.MockTransport(lambda request: httpx.Response(401)),
    )

    with pytest.raises(DeliveryError) as error:
        channel.send(NOTIFICATION, Destination(None, "42", None, None))

    assert "SECRET" not in str(error.value)


def test_push_is_reported_as_unavailable():
    assert ChannelRegistry([]).available()[NotificationChannel.PUSH] is False


# --- Criptografia ----------------------------------------------------------------------


def test_cipher_roundtrip_and_tamper_detection():
    cipher = SecretCipher(Fernet.generate_key().decode())
    other = SecretCipher(Fernet.generate_key().decode())
    token = cipher.encrypt("refresh-token")

    assert token != "refresh-token"
    assert cipher.decrypt(token) == "refresh-token"
    with pytest.raises(DomainError):
        other.decrypt(token)


def test_cipher_without_key_refuses_to_store_secrets():
    with pytest.raises(EncryptionNotConfiguredError):
        SecretCipher(None).encrypt("x")


# --- Clientes OAuth (transporte simulado: nenhuma chamada real) ------------------------


def gmail(handler) -> GmailClient:
    return GmailClient(
        client_id="cid", client_secret="csecret", transport=httpx.MockTransport(handler)
    )


def test_authorization_url_uses_pkce_state_and_readonly_scope():
    url = gmail(lambda r: httpx.Response(200)).authorization_url(
        state="estado", code_challenge="desafio", redirect_uri="http://localhost/cb"
    )

    params = parse_qs(urlsplit(url).query)
    assert params["state"] == ["estado"]
    assert params["code_challenge"] == ["desafio"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["scope"] == ["https://www.googleapis.com/auth/gmail.readonly"]


def test_code_exchange_sends_verifier_and_parses_tokens():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(parse_qs(request.content.decode()))
        return httpx.Response(
            200,
            json={"access_token": "at", "refresh_token": "rt", "expires_in": 3600, "scope": "s1"},
        )

    tokens = gmail(handler).exchange_code(code="c", code_verifier="v", redirect_uri="http://x/cb")

    assert captured["code_verifier"] == ["v"] and captured["grant_type"] == ["authorization_code"]
    assert tokens.refresh_token == "rt" and tokens.expires_at is not None


def test_gmail_reads_only_metadata():
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url)
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "m1"}]})
        return httpx.Response(
            200,
            json={
                "id": "m1",
                "threadId": "t1",
                "snippet": "Convite",
                "internalDate": "1763640000000",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Entrevista"},
                        {"name": "From", "value": "rh@x.example"},
                    ]
                },
            },
        )

    messages = gmail(handler).list_messages("token", NOW, 10)

    assert messages[0].subject == "Entrevista" and messages[0].from_address == "rh@x.example"
    assert requested[1].params["format"] == "metadata"  # o corpo nunca é baixado


def test_outlook_parses_graph_messages():
    body = {
        "value": [
            {
                "id": "o1",
                "conversationId": "c1",
                "subject": "Teste técnico",
                "from": {"emailAddress": {"name": "RH", "address": "rh@y.example"}},
                "receivedDateTime": "2026-11-20T10:00:00Z",
                "bodyPreview": "Prazo até 22/11",
            }
        ]
    }
    client = OutlookClient(
        client_id="c",
        client_secret="s",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)),
    )

    message = client.list_messages("token", NOW, 10)[0]

    assert message.from_address == "RH <rh@y.example>"
    assert message.received_at == datetime(2026, 11, 20, 10, 0, tzinfo=UTC)


def test_provider_http_error_does_not_leak_response_body():
    client = gmail(lambda r: httpx.Response(400, json={"error": "invalid_grant code=abc"}))

    with pytest.raises(EmailProviderError) as error:
        client.exchange_code(code="abc", code_verifier="v", redirect_uri="http://x/cb")

    assert "abc" not in str(error.value)
