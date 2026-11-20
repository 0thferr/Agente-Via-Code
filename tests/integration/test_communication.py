import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, update

from app.core.crypto import SecretCipher
from app.integrations.email.base import EmailClient, EmailMessage, OAuthTokens
from app.integrations.email.simulated import SimulatedEmailClient, default_messages
from app.integrations.notifications.channels import (
    ChannelRegistry,
    TelegramChannel,
    WebhookChannel,
)
from app.main import app
from app.models.application import Application
from app.models.communication import (
    EmailAccount,
    NotificationDelivery,
    OAuthState,
    RecruitmentMessage,
)
from app.models.enums import ApplicationStatus, DeliveryStatus, EmailProvider, NotificationEvent
from app.services.communication import email_service, notification_service
from app.wiring import get_cipher, get_email_clients
from tests.conftest import create_test_user, login
from tests.integration.test_analysis import setup_jobs

CIPHER = SecretCipher(Fernet.generate_key().decode())


class FakeOAuthClient(EmailClient):
    provider = EmailProvider.GMAIL

    def __init__(self) -> None:
        self.exchanges: list[dict] = []

    def authorization_url(self, *, state, code_challenge, redirect_uri):
        return f"https://auth.example/authorize?state={state}&code_challenge={code_challenge}"

    def exchange_code(self, *, code, code_verifier, redirect_uri):
        self.exchanges.append({"code": code, "verifier": code_verifier})
        return OAuthTokens("access-123", "refresh-456", datetime.now(UTC) + timedelta(hours=1), [])

    def account_email(self, access_token):
        return "pessoa@gmail.example"

    def list_messages(self, access_token, since, limit):
        return []


@pytest.fixture(autouse=True)
def cipher():
    app.dependency_overrides[get_cipher] = lambda: CIPHER
    return CIPHER


def use_clients(clients) -> None:
    app.dependency_overrides[get_email_clients] = lambda: clients


def headers_for(client, db_session, email):
    user = create_test_user(db_session, email)
    return user, {"Authorization": f"Bearer {login(client, user.email)['access_token']}"}


# --- Integrações e OAuth ---------------------------------------------------------------


def test_overview_reports_real_state_of_integrations(client, auth_headers):
    use_clients({EmailProvider.SIMULATED: SimulatedEmailClient()})
    client.post("/api/v1/integrations/simulated/connect", headers=auth_headers)

    overview = client.get("/api/v1/integrations", headers=auth_headers).json()

    email = {item["provider"]: item for item in overview["email"]}
    assert email["simulated"]["configured"] and len(email["simulated"]["accounts"]) == 1
    assert email["gmail"]["configured"] is False
    assert overview["notification_channels"]["push"] is False
    providers = {item["key"]: item["integration_status"] for item in overview["job_providers"]}
    assert providers["linkedin"] == "manual_only"


def test_unconfigured_provider_cannot_be_connected(client, auth_headers):
    use_clients({})

    response = client.post("/api/v1/integrations/gmail/connect", headers=auth_headers)

    assert response.status_code == 422


def test_oauth_flow_with_state_and_pkce(client, db_session, auth_headers):
    fake = FakeOAuthClient()
    use_clients({EmailProvider.GMAIL: fake})

    url = client.post("/api/v1/integrations/gmail/connect", headers=auth_headers).json()[
        "authorization_url"
    ]
    params = parse_qs(urlsplit(url).query)
    callback = client.get(
        "/api/v1/integrations/gmail/callback",
        params={"state": params["state"][0], "code": "codigo"},
    )
    reuse = client.get(
        "/api/v1/integrations/gmail/callback",
        params={"state": params["state"][0], "code": "codigo"},
    )

    assert callback.status_code == 200, callback.text
    assert callback.json()["email_address"] == "pessoa@gmail.example"
    assert reuse.status_code == 422  # state é de uso único
    # PKCE: o verifier enviado na troca corresponde ao challenge da URL.
    verifier = fake.exchanges[0]["verifier"]
    import base64

    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    assert challenge.decode() == params["code_challenge"][0]
    # Tokens só existem cifrados no banco.
    account = db_session.scalars(select(EmailAccount)).one()
    assert "access-123" not in account.encrypted_access_token
    assert CIPHER.decrypt(account.encrypted_refresh_token) == "refresh-456"


def test_expired_or_unknown_state_is_rejected(client, db_session, auth_headers):
    use_clients({EmailProvider.GMAIL: FakeOAuthClient()})
    url = client.post("/api/v1/integrations/gmail/connect", headers=auth_headers).json()[
        "authorization_url"
    ]
    state = parse_qs(urlsplit(url).query)["state"][0]
    db_session.execute(
        update(OAuthState).values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
    )

    expired = client.get(
        "/api/v1/integrations/gmail/callback", params={"state": state, "code": "c"}
    )
    unknown = client.get(
        "/api/v1/integrations/gmail/callback", params={"state": "x" * 32, "code": "c"}
    )

    assert expired.status_code == 422 and unknown.status_code == 422


def test_disconnect_removes_account_and_messages(client, db_session, auth_headers):
    use_clients({EmailProvider.SIMULATED: SimulatedEmailClient()})
    client.post("/api/v1/integrations/simulated/connect", headers=auth_headers)

    first = client.delete("/api/v1/integrations/simulated/disconnect", headers=auth_headers)
    second = client.delete("/api/v1/integrations/simulated/disconnect", headers=auth_headers)

    assert first.status_code == 204 and second.status_code == 404
    assert db_session.scalars(select(EmailAccount)).all() == []


# --- Monitoramento de e-mails ----------------------------------------------------------


def applied_applications(client, db_session, headers, external_ids) -> dict[str, str]:
    jobs = setup_jobs(client, headers)
    ids = {}
    for external_id in external_ids:
        created = client.post(
            "/api/v1/applications", json={"job_id": jobs[external_id]}, headers=headers
        ).json()
        ids[external_id] = created["id"]
    db_session.execute(
        update(Application)
        .where(Application.id.in_(ids.values()))
        .values(status=ApplicationStatus.APPLIED)
    )
    return ids


def sync(db_session, user_id, client_impl, now=None):
    clients = {EmailProvider.SIMULATED: client_impl}
    return email_service.sync_user_accounts(
        db_session, clients, CIPHER, user_id, timedelta(days=14), now or datetime.now(UTC)
    )


def connect_simulated(db_session, user):
    email_service.connect_simulated(db_session, SimulatedEmailClient(), CIPHER, user.id)


def test_sync_classifies_matches_and_updates_applications(client, db_session, user, auth_headers):
    ids = applied_applications(client, db_session, auth_headers, ["mock-001", "mock-007"])
    connect_simulated(db_session, user)
    now = datetime.now(UTC)

    totals = sync(db_session, user.id, SimulatedEmailClient(default_messages(now)), now)

    messages = {
        m["subject"]: m
        for m in client.get("/api/v1/messages", headers=auth_headers).json()["items"]
    }
    acme = client.get(f"/api/v1/applications/{ids['mock-001']}", headers=auth_headers).json()
    zeta = client.get(f"/api/v1/applications/{ids['mock-007']}", headers=auth_headers).json()
    notifications = client.get("/api/v1/notifications", headers=auth_headers).json()
    assert totals["new_messages"] == 4
    assert acme["status"] == "interview"
    assert zeta["status"] == "assessment"
    # A newsletter não é de recrutamento: não aparece na listagem e, no banco, guarda
    # só a referência (id e data), sem remetente, assunto ou trecho.
    assert "As 10 linguagens mais usadas em 2026" not in messages
    assert len(messages) == 3
    newsletter = db_session.scalars(
        select(RecruitmentMessage).where(RecruitmentMessage.provider_message_id == "sim-003")
    ).one()
    assert newsletter.category.value == "not_recruiting"
    assert (newsletter.from_address, newsletter.subject, newsletter.snippet) == ("", "", "")
    assessment = next(m for m in messages.values() if m["category"] == "assessment")
    assert assessment["deadline_at"] is not None
    events = {n["event_type"] for n in notifications["items"]}
    assert {"interview_invite", "assessment_received"} <= events
    # Só metadados: nenhum campo de corpo do e-mail é exposto ou guardado.
    assert all("body" not in message for message in messages.values())


def test_sync_is_idempotent(client, db_session, user, auth_headers):
    applied_applications(client, db_session, auth_headers, ["mock-001"])
    connect_simulated(db_session, user)
    now = datetime.now(UTC)
    inbox = SimulatedEmailClient(default_messages(now))
    sync(db_session, user.id, inbox, now)
    db_session.execute(update(EmailAccount).values(last_synced_at=None))

    second = sync(db_session, user.id, inbox, now)

    assert second["new_messages"] == 0


def test_ambiguous_message_needs_user_validation(client, db_session, user, auth_headers):
    ids = applied_applications(client, db_session, auth_headers, ["mock-001", "mock-005"])
    connect_simulated(db_session, user)
    now = datetime.now(UTC)
    ambiguous = EmailMessage(
        "amb-1", None, "noreply@ats.example", "Convite para entrevista na Acme Tech", "", now
    )

    sync(db_session, user.id, SimulatedEmailClient([ambiguous]), now)
    message = client.get(
        "/api/v1/messages", params={"match_status": "needs_review"}, headers=auth_headers
    ).json()["items"][0]
    untouched = client.get(f"/api/v1/applications/{ids['mock-001']}", headers=auth_headers).json()
    confirmed = client.patch(
        f"/api/v1/messages/{message['id']}/association",
        json={"application_id": ids["mock-005"]},
        headers=auth_headers,
    ).json()
    chosen = client.get(f"/api/v1/applications/{ids['mock-005']}", headers=auth_headers).json()

    assert untouched["status"] == "applied"  # nada muda antes da validação
    assert confirmed["match_status"] == "matched"
    assert chosen["status"] == "interview"
    review_notifications = client.get("/api/v1/notifications", headers=auth_headers).json()
    assert "message_needs_review" in {n["event_type"] for n in review_notifications["items"]}


def test_upcoming_deadline_is_notified_once(client, db_session, user, auth_headers):
    applied_applications(client, db_session, auth_headers, ["mock-007"])
    connect_simulated(db_session, user)
    now = datetime.now(UTC)
    sync(db_session, user.id, SimulatedEmailClient(default_messages(now)), now)

    first = email_service.notify_upcoming_deadlines(db_session, user.id, now)
    second = email_service.notify_upcoming_deadlines(db_session, user.id, now)

    assert first == 1 and second == 0


def test_messages_are_private(client, db_session, user, auth_headers):
    applied_applications(client, db_session, auth_headers, ["mock-001"])
    connect_simulated(db_session, user)
    sync(db_session, user.id, SimulatedEmailClient(default_messages()))
    message_id = client.get("/api/v1/messages", headers=auth_headers).json()["items"][0]["id"]
    _, other_headers = headers_for(client, db_session, "other@example.com")

    response = client.patch(
        f"/api/v1/messages/{message_id}/association",
        json={"application_id": None},
        headers=other_headers,
    )

    assert response.status_code == 404


# --- Notificações ----------------------------------------------------------------------


def test_preferences_defaults_and_webhook_secret_shown_once(client, user, auth_headers):
    defaults = client.get("/api/v1/notifications/preferences", headers=auth_headers).json()
    saved = client.put(
        "/api/v1/notifications/preferences",
        json={"channels": ["webhook"], "webhook_url": "https://hooks.example.com/jp"},
        headers=auth_headers,
    ).json()
    fetched = client.get("/api/v1/notifications/preferences", headers=auth_headers).json()
    unsafe = client.put(
        "/api/v1/notifications/preferences",
        json={"webhook_url": "http://hooks.example.com/jp"},
        headers=auth_headers,
    )

    assert defaults["email_to"] == user.email
    assert saved["webhook_secret"] and saved["webhook_secret_configured"]
    assert fetched["webhook_secret"] is None and fetched["webhook_secret_configured"]
    assert unsafe.status_code == 422


def notify(db_session, user_id, event=NotificationEvent.INTERVIEW_INVITE, dedup_key=None):
    result = notification_service.notify(
        db_session, user_id, event, title="t", body="b", dedup_key=dedup_key
    )
    db_session.commit()
    return result


def test_dedup_and_disabled_events(client, db_session, user, auth_headers):
    client.put(
        "/api/v1/notifications/preferences",
        json={"disabled_events": ["daily_summary"]},
        headers=auth_headers,
    )

    first = notify(db_session, user.id, dedup_key="same-fact")
    duplicate = notify(db_session, user.id, dedup_key="same-fact")
    disabled = notify(db_session, user.id, NotificationEvent.DAILY_SUMMARY)

    assert first is not None and duplicate is None and disabled is None


def test_list_and_mark_read(client, db_session, user, auth_headers):
    created = notify(db_session, user.id)

    before = client.get("/api/v1/notifications", headers=auth_headers).json()
    read = client.patch(f"/api/v1/notifications/{created.id}/read", headers=auth_headers)
    after = client.get(
        "/api/v1/notifications", params={"unread_only": True}, headers=auth_headers
    ).json()

    assert before["unread"] == 1
    assert read.json()["read_at"] is not None
    assert after["total"] == 0


def test_webhook_delivery_signed_and_frequency_controlled(client, db_session, user, auth_headers):
    secret = client.put(
        "/api/v1/notifications/preferences",
        json={
            "channels": ["webhook", "telegram"],
            "webhook_url": "https://hooks.example.com/jp",
            "telegram_chat_id": "42",
            "min_interval_minutes": {"interview_invite": 60},
        },
        headers=auth_headers,
    ).json()["webhook_secret"]
    received = []

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(200)

    channels = ChannelRegistry(
        [
            WebhookChannel(timeout_seconds=5, transport=httpx.MockTransport(handler)),
            TelegramChannel(bot_token=None, timeout_seconds=5),  # não configurado no servidor
        ]
    )
    notify(db_session, user.id)
    notify(db_session, user.id)  # dentro do intervalo mínimo: entrega externa pulada

    sent = notification_service.deliver_pending(db_session, channels, CIPHER, datetime.now(UTC))

    statuses = sorted(
        (d.channel.value, d.status.value) for d in db_session.scalars(select(NotificationDelivery))
    )
    assert sent == 1 and len(received) == 1
    signature = hmac.new(secret.encode(), received[0].content, hashlib.sha256).hexdigest()
    assert received[0].headers["X-JobPilot-Signature"] == f"sha256={signature}"
    assert statuses == [
        ("telegram", "skipped"),
        ("telegram", "skipped"),
        ("webhook", "sent"),
        ("webhook", "skipped"),
    ]


def test_failed_delivery_is_retried_then_marked_failed(client, db_session, user, auth_headers):
    client.put(
        "/api/v1/notifications/preferences",
        json={"channels": ["webhook"], "webhook_url": "https://hooks.example.com/jp"},
        headers=auth_headers,
    )
    channels = ChannelRegistry(
        [
            WebhookChannel(
                timeout_seconds=5, transport=httpx.MockTransport(lambda r: httpx.Response(500))
            )
        ]
    )
    notify(db_session, user.id)

    for _ in range(3):
        notification_service.deliver_pending(db_session, channels, CIPHER, datetime.now(UTC))

    delivery = db_session.scalars(select(NotificationDelivery)).one()
    assert delivery.status is DeliveryStatus.FAILED and delivery.attempts == 3


def test_high_match_and_approval_notifications_from_pipeline(client, auth_headers):
    jobs = setup_jobs(client, auth_headers)
    # Pesos só em critérios atendidos: nota 100 (alta compatibilidade).
    client.put(
        "/api/v1/ai/preferences",
        json={
            "weights": {"skills": 0, "seniority": 1, "salary": 1, "location": 1, "work_model": 1}
        },
        headers=auth_headers,
    )
    application = client.post(
        "/api/v1/applications", json={"job_id": jobs["mock-001"]}, headers=auth_headers
    ).json()
    client.post(f"/api/v1/applications/{application['id']}/prepare", json={}, headers=auth_headers)

    events = {
        n["event_type"]
        for n in client.get("/api/v1/notifications", headers=auth_headers).json()["items"]
    }

    assert {"high_match_job", "approval_pending"} <= events
