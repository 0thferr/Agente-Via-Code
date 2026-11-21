"""Segunda passada de revisão: automação, notificações e log de erros."""

from datetime import UTC, datetime, timedelta

import httpx
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.automation import runner, tasks
from app.core.crypto import SecretCipher
from app.integrations.notifications.channels import ChannelRegistry, WebhookChannel
from app.main import app, create_app
from app.models.automation import ScheduledJob
from app.models.communication import NotificationDelivery
from app.models.enums import DeliveryStatus, JobRunStatus, NotificationEvent
from app.services.communication import notification_service
from app.wiring import get_cipher
from tests.integration.test_automation import (  # noqa: F401  (fixture context)
    LEASE,
    context,
    create_rule,
    run_now,
    tick,
)

CIPHER = SecretCipher(Fernet.generate_key().decode())


# --- Regra desativada não executa ------------------------------------------------------


def test_pending_job_of_disabled_rule_is_not_executed(
    client,
    db_session,
    auth_headers,
    context,  # noqa: F811
    monkeypatch,
):
    def must_not_run(*args):
        raise AssertionError("tarefa de regra desativada foi executada")

    monkeypatch.setitem(tasks.TASK_HANDLERS, tasks.AutomationTask.DAILY_SUMMARY, must_not_run)
    rule = create_rule(client, auth_headers, "daily_summary")
    job_id = run_now(client, auth_headers, rule["id"])["id"]
    client.patch(
        f"/api/v1/automations/{rule['id']}", json={"is_enabled": False}, headers=auth_headers
    )

    tick(db_session, context)

    job = db_session.get(ScheduledJob, job_id)
    assert job.status is JobRunStatus.FAILED
    assert job.last_error == "Regra desativada antes da execução."


def test_disabled_rule_cannot_be_run_now(client, auth_headers):
    rule = create_rule(client, auth_headers, "daily_summary", is_enabled=False)

    response = client.post(f"/api/v1/automations/{rule['id']}/run", headers=auth_headers)

    assert response.status_code == 422


# --- Tentativa interrompida fica no histórico ------------------------------------------


def test_interrupted_attempt_is_logged_when_job_is_reclaimed(client, db_session, auth_headers):
    rule = create_rule(client, auth_headers, "daily_summary")
    run_now(client, auth_headers, rule["id"])
    now = datetime.now(UTC)
    runner.claim_next_job(db_session, "worker-a", now, LEASE)  # worker-a "morre" aqui

    reclaimed = runner.claim_next_job(db_session, "worker-b", now + LEASE * 2, LEASE)

    assert [(log.attempt, log.status) for log in reclaimed.logs] == [(1, JobRunStatus.FAILED)]
    assert "interrompida" in reclaimed.logs[0].error


# --- Entrega de notificações com espera entre tentativas -------------------------------


def test_failed_delivery_waits_before_retrying(client, db_session, user, auth_headers):
    app.dependency_overrides[get_cipher] = lambda: CIPHER
    client.put(
        "/api/v1/notifications/preferences",
        json={"channels": ["webhook"], "webhook_url": "https://hooks.example.com/x"},
        headers=auth_headers,
    )
    attempts: list[httpx.Request] = []

    def failing(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        return httpx.Response(503)

    channels = ChannelRegistry(
        [WebhookChannel(timeout_seconds=5, transport=httpx.MockTransport(failing))]
    )
    notification_service.notify(
        db_session, user.id, NotificationEvent.INTERVIEW_INVITE, title="t", body="b"
    )
    db_session.commit()
    now = datetime.now(UTC)

    notification_service.deliver_pending(db_session, channels, CIPHER, now)
    notification_service.deliver_pending(db_session, channels, CIPHER, now + timedelta(seconds=30))
    notification_service.deliver_pending(db_session, channels, CIPHER, now + timedelta(minutes=2))

    delivery = db_session.scalars(select(NotificationDelivery)).one()
    assert len(attempts) == 2  # a segunda chamada (30s depois) respeitou a espera
    assert delivery.status is DeliveryStatus.PENDING
    assert delivery.next_attempt_at > now + timedelta(minutes=2)


# --- Erro inesperado registrado com request_id -----------------------------------------


def test_unhandled_error_is_logged_with_request_id(monkeypatch):
    from app import main

    logged: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        main.logger, "exception", lambda message, extra: logged.append((message, extra))
    )
    failing_app = create_app()

    @failing_app.get("/boom")
    def boom():
        raise RuntimeError("falha inesperada")

    response = TestClient(failing_app, raise_server_exceptions=False).get(
        "/boom", headers={"X-Request-ID": "req-123"}
    )

    assert response.status_code == 500
    assert logged and logged[0][0] == "request_failed"
    assert logged[0][1]["request_id"] == "req-123"
