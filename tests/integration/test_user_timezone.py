"""Fuso horário do usuário aplicado a limites diários, prazos e dashboard."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from app.models.application import Application
from app.models.enums import ApplicationStatus, ConfirmationSource
from app.models.job import JobOpportunity
from app.services.applications import policy
from tests.integration.test_analysis import setup_jobs


def test_me_shows_default_timezone_and_patch_updates_it(client, auth_headers):
    default = client.get("/api/v1/auth/me", headers=auth_headers).json()
    updated = client.patch(
        "/api/v1/auth/me", json={"timezone": "America/Manaus"}, headers=auth_headers
    )
    invalid = client.patch(
        "/api/v1/auth/me", json={"timezone": "Mars/Olympus"}, headers=auth_headers
    )

    assert default["timezone"] == "America/Sao_Paulo"
    assert updated.status_code == 200 and updated.json()["timezone"] == "America/Manaus"
    assert invalid.status_code == 422


def test_daily_limit_resets_at_local_midnight(client, db_session, user, auth_headers):
    jobs = setup_jobs(client, auth_headers)
    application = client.post(
        "/api/v1/applications", json={"job_id": jobs["mock-001"]}, headers=auth_headers
    ).json()
    # Enviada às 02:30 UTC do dia 21 = 23:30 do dia 20 em São Paulo.
    db_session.execute(
        update(Application)
        .where(Application.id == application["id"])
        .values(
            status=ApplicationStatus.APPLIED,
            submitted_at=datetime(2026, 11, 21, 2, 30, tzinfo=UTC),
            confirmation_source=ConfirmationSource.SIMULATED,
        )
    )

    # 01:00 do dia 21 em São Paulo: novo dia local, o envio de ontem não conta.
    new_local_day = datetime(2026, 11, 21, 4, 0, tzinfo=UTC)
    # 23:50 do dia 20 em São Paulo: ainda o mesmo dia local, conta.
    same_local_day = datetime(2026, 11, 21, 2, 50, tzinfo=UTC)

    assert policy.submissions_today(db_session, user.id, now=new_local_day) == 0
    assert policy.submissions_today(db_session, user.id, now=same_local_day) == 1


def test_dashboard_timeline_groups_by_local_day(client, db_session, auth_headers):
    setup_jobs(client, auth_headers)
    # 01:00 UTC do dia 21 = 22:00 do dia 20 em São Paulo.
    db_session.execute(
        update(JobOpportunity).values(created_at=datetime(2026, 11, 21, 1, 0, tzinfo=UTC))
    )

    body = client.get(
        "/api/v1/dashboard/timeline",
        params={
            "period": "day",
            "date_from": "2026-11-19T00:00:00Z",
            "date_to": "2026-11-22T00:00:00Z",
        },
        headers=auth_headers,
    ).json()

    starts = [point["period_start"] for point in body["points"] if point["jobs_found"]]
    assert starts == ["2026-11-20T03:00:00Z"]  # meia-noite do dia 20 em São Paulo


def test_deadline_notification_shows_local_time(client, db_session, user, auth_headers):
    from cryptography.fernet import Fernet

    from app.core.crypto import SecretCipher
    from app.integrations.email.base import EmailMessage
    from app.integrations.email.simulated import SimulatedEmailClient
    from app.models.enums import EmailProvider
    from app.services.communication import email_service

    cipher = SecretCipher(Fernet.generate_key().decode())
    email_service.connect_simulated(db_session, SimulatedEmailClient(), cipher, user.id)
    now = datetime.now(UTC)
    tomorrow = (now + timedelta(days=1)).astimezone().strftime("%d/%m")
    message = EmailMessage(
        "d1", None, "rh@x.example", "Teste técnico", f"Entregar até {tomorrow}", now
    )
    email_service.sync_user_accounts(
        db_session,
        {EmailProvider.SIMULATED: SimulatedEmailClient([message])},
        cipher,
        user.id,
        timedelta(days=14),
        now,
    )

    email_service.notify_upcoming_deadlines(db_session, user.id, now)

    notifications = client.get("/api/v1/notifications", headers=auth_headers).json()["items"]
    deadline = next(n for n in notifications if n["event_type"] == "deadline_approaching")
    assert "23:59" in deadline["body"] and "America/Sao_Paulo" in deadline["body"]
