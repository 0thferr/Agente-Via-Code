"""Regressões encontradas na revisão de código do PR #1 (com banco)."""

from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet
from sqlalchemy import select

from app.automation import tasks
from app.core.crypto import SecretCipher
from app.integrations.email.base import EmailMessage
from app.integrations.email.simulated import SimulatedEmailClient
from app.models.analysis import JobAnalysis
from app.models.communication import EmailAccount, RecruitmentMessage
from app.models.enums import AnalysisStatus, EmailProvider, Recommendation
from app.schemas.profile import ProfileIn
from app.services import profile_service
from app.services.communication import email_service
from tests.integration.test_analysis import PROFILE, setup_jobs
from tests.integration.test_automation import context  # noqa: F401  (fixture)

CIPHER = SecretCipher(Fernet.generate_key().decode())


def test_profile_version_changes_when_only_nested_lists_change(db_session, user):
    first = profile_service.save_profile(db_session, user.id, ProfileIn(**PROFILE))
    version = first.updated_at
    changed = {**PROFILE, "skills": [*PROFILE["skills"], {"name": "SQL", "category": "technology"}]}

    second = profile_service.save_profile(db_session, user.id, ProfileIn(**changed))

    assert second.updated_at > version


def test_job_with_failed_analysis_is_retried_by_worker(
    client,
    db_session,
    user,
    auth_headers,
    context,  # noqa: F811
):
    jobs = setup_jobs(client, auth_headers)
    job_id = jobs["mock-001"]
    db_session.add(
        JobAnalysis(
            user_id=user.id,
            job_id=job_id,
            status=AnalysisStatus.FAILED,
            recommendation=Recommendation.PENDING,
            min_score=65,
            weights={},
            analyzer_provider="openai",
            analyzer_model="x",
            profile_version="v",
            error="timeout",
        )
    )
    db_session.flush()

    tasks.analyze_jobs(db_session, context, user.id, {"max_items": 50})

    completed = db_session.scalars(
        select(JobAnalysis).where(
            JobAnalysis.job_id == job_id, JobAnalysis.status == AnalysisStatus.COMPLETED
        )
    ).all()
    assert len(completed) == 1


def _connect(db_session, user):
    email_service.connect_simulated(db_session, SimulatedEmailClient(), CIPHER, user.id)
    return db_session.scalars(select(EmailAccount)).one()


def _sync(db_session, user, messages, now):
    return email_service.sync_user_accounts(
        db_session,
        {EmailProvider.SIMULATED: SimulatedEmailClient(messages)},
        CIPHER,
        user.id,
        timedelta(days=14),
        now,
    )


def test_large_inbox_is_processed_across_syncs_without_losing_messages(db_session, user):
    account = _connect(db_session, user)
    now = datetime.now(UTC)
    inbox = [
        EmailMessage(
            f"m{i}",
            None,
            "rh@x.example",
            f"Vaga {i}",
            "processo seletivo",
            now - timedelta(minutes=i),
        )
        for i in range(email_service.MAX_MESSAGES_PER_SYNC + 10)
    ]

    first = _sync(db_session, user, inbox, now)
    cursor_after_first = account.last_synced_at
    second = _sync(db_session, user, inbox, now + timedelta(minutes=5))

    assert first["new_messages"] == email_service.MAX_MESSAGES_PER_SYNC
    assert cursor_after_first is None  # ainda há mensagens antigas: o cursor não avança
    assert second["new_messages"] == 10
    assert db_session.scalar(
        select(RecruitmentMessage.id).where(RecruitmentMessage.provider_message_id == "m59")
    )


def test_non_recruiting_email_keeps_only_reference(client, db_session, user, auth_headers):
    _connect(db_session, user)
    now = datetime.now(UTC)
    personal = EmailMessage(
        "p1", None, "Banco <conta@banco.example>", "Sua fatura chegou", "Valor: R$ 1.234", now
    )

    _sync(db_session, user, [personal], now)

    stored = db_session.scalars(select(RecruitmentMessage)).one()
    assert (stored.from_address, stored.subject, stored.snippet) == ("", "", "")
    assert client.get("/api/v1/messages", headers=auth_headers).json()["total"] == 0


# --- Limite diário com envios simultâneos ----------------------------------------------


def test_in_flight_submission_counts_toward_daily_limit(client, db_session, auth_headers):
    from sqlalchemy import update

    from app.models.application import Application
    from app.models.enums import ApplicationStatus
    from tests.integration.test_applications import ready_application

    application = ready_application(client, auth_headers)
    # Outra candidatura sendo enviada neste momento (APPLYING, ainda sem submitted_at).
    jobs = client.get("/api/v1/jobs", params={"limit": 100}, headers=auth_headers).json()["items"]
    other_job = next(job for job in jobs if job["id"] != application["job_id"])
    other = client.post(
        "/api/v1/applications", json={"job_id": other_job["id"]}, headers=auth_headers
    )
    db_session.execute(
        update(Application)
        .where(Application.id == other.json()["id"])
        .values(status=ApplicationStatus.APPLYING)
    )
    client.put(
        "/api/v1/applications/settings", json={"max_daily_submissions": 1}, headers=auth_headers
    )

    response = client.post(f"/api/v1/applications/{application['id']}/submit", headers=auth_headers)

    assert response.status_code == 422
    assert "Limite diário" in response.json()["error"]["message"]


def test_submission_checks_run_under_per_user_lock(db_session, user):
    from sqlalchemy import text

    from app.services.applications import submission_service

    submission_service.lock_user_submissions(db_session, user.id)

    advisory_locks = db_session.scalar(
        text(
            "SELECT count(*) FROM pg_locks "
            "WHERE locktype = 'advisory' AND pid = pg_backend_pid() AND granted"
        )
    )
    assert advisory_locks == 1
