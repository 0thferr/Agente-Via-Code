from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.automation import runner, tasks
from app.automation.tasks import TaskContext
from app.core.errors import IntegrationError, ValidationError
from app.integrations.application_submitters.mock import MockApplicationSubmitter
from app.integrations.application_submitters.registry import SubmitterRegistry
from app.integrations.job_providers.registry import build_default_registry
from app.models.application import Application
from app.models.automation import AutomationRule, ScheduledJob
from app.models.enums import ApplicationStatus, JobRunStatus
from app.services.analysis.requirements import KeywordRequirementAnalyzer
from app.services.cover_letters.writers import TemplateCoverLetterWriter
from tests.integration.test_analysis import PROFILE, setup_jobs
from tests.integration.test_resume_import import RESUME_LINES, upload
from tests.pdf_factory import make_pdf

BASE = "/api/v1/automations"
LEASE = timedelta(minutes=15)


@pytest.fixture
def context():
    return TaskContext(
        providers=build_default_registry(include_simulated=True),
        analyzer=KeywordRequirementAnalyzer(),
        writer=TemplateCoverLetterWriter(),
        submitters=SubmitterRegistry([MockApplicationSubmitter()]),
    )


def tick(db_session, context, now=None, max_jobs=10):
    # Usa a sessão do teste (transação desfeita no fim) no lugar de sessões novas.
    return runner.run_tick(
        lambda: nullcontext(db_session),
        context,
        worker_id="test-worker",
        now=now or datetime.now(UTC),
        lease=LEASE,
        stale_submission_after=timedelta(minutes=15),
        max_jobs=max_jobs,
    )


def create_rule(client, headers, task_type, **extra) -> dict:
    payload = {"name": task_type, "task_type": task_type, "interval_minutes": 60, **extra}
    response = client.post(BASE, json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def run_now(client, headers, rule_id) -> dict:
    response = client.post(f"{BASE}/{rule_id}/run", headers=headers)
    assert response.status_code == 202, response.text
    return response.json()


# --- API -------------------------------------------------------------------------------


def test_create_rule_computes_next_run(client, auth_headers):
    rule = create_rule(
        client,
        auth_headers,
        "discover_jobs",
        interval_minutes=None,
        time_of_day="08:00",
        days_of_week=[0, 1, 2, 3, 4],
        timezone="America/Sao_Paulo",
    )

    next_run = datetime.fromisoformat(rule["next_run_at"])
    assert next_run > datetime.now(UTC)
    assert next_run.astimezone(UTC).hour == 11  # 08:00 em São Paulo
    assert rule["config"]["filters"]["keywords"] == []


def test_email_tasks_can_be_scheduled(client, auth_headers):
    for task_type in ("check_emails", "check_deadlines"):
        response = client.post(
            BASE,
            json={"name": task_type, "task_type": task_type, "interval_minutes": 60},
            headers=auth_headers,
        )
        assert response.status_code == 201, response.text


def test_task_without_handler_is_rejected(client, auth_headers, monkeypatch):
    monkeypatch.delitem(tasks.TASK_HANDLERS, tasks.AutomationTask.DAILY_SUMMARY)

    response = client.post(
        BASE,
        json={"name": "x", "task_type": "daily_summary", "interval_minutes": 60},
        headers=auth_headers,
    )

    assert response.status_code == 422
    assert "não está disponível" in response.json()["error"]["message"]


def test_patch_switches_schedule_kind_and_revalidates(client, auth_headers):
    rule = create_rule(client, auth_headers, "analyze_jobs")

    switched = client.patch(
        f"{BASE}/{rule['id']}", json={"time_of_day": "09:30"}, headers=auth_headers
    )
    invalid = client.patch(
        f"{BASE}/{rule['id']}", json={"config": {"max_items": 999}}, headers=auth_headers
    )

    assert switched.json()["interval_minutes"] is None
    assert switched.json()["time_of_day"] == "09:30"
    assert invalid.status_code == 422


def test_run_now_is_idempotent_within_the_minute(client, auth_headers):
    rule = create_rule(client, auth_headers, "analyze_jobs")

    first = run_now(client, auth_headers, rule["id"])
    second = run_now(client, auth_headers, rule["id"])

    assert first["id"] == second["id"]
    assert first["status"] == "pending"


def test_rules_are_private(client, db_session, auth_headers):
    from tests.conftest import create_test_user, login

    rule = create_rule(client, auth_headers, "analyze_jobs")
    other = create_test_user(db_session, "other@example.com")
    headers = {"Authorization": f"Bearer {login(client, other.email)['access_token']}"}

    assert client.post(f"{BASE}/{rule['id']}/run", headers=headers).status_code == 404
    assert client.get(BASE, headers=headers).json() == []


# --- Pipeline completo pelo worker ------------------------------------------------------


def test_worker_runs_full_pipeline_in_automatic_mode(client, db_session, auth_headers, context):
    setup_jobs(client, auth_headers)
    client.put(
        "/api/v1/profile", json={**PROFILE, "availability": "Imediata"}, headers=auth_headers
    )
    upload(client, auth_headers, make_pdf(RESUME_LINES))
    client.put(
        "/api/v1/applications/settings",
        json={"mode": "automatic", "auto_submit_providers": ["mock"]},
        headers=auth_headers,
    )
    for task_type in ("analyze_jobs", "prepare_applications", "submit_applications"):
        rule = create_rule(client, auth_headers, task_type, config={"max_items": 20})
        run_now(client, auth_headers, rule["id"])
        tick(db_session, context)

    executions = client.get(f"{BASE}/executions", headers=auth_headers).json()
    applications = client.get("/api/v1/applications", headers=auth_headers).json()["items"]

    assert {item["status"] for item in executions["items"]} == {"succeeded"}
    summaries = {item["task_type"]: item["logs"][0]["summary"] for item in executions["items"]}
    assert summaries["analyze_jobs"]["analyzed"] == 7
    assert summaries["submit_applications"]["submitted"] >= 1
    assert "applied" in {application["status"] for application in applications}
    applied = next(a for a in applications if a["status"] == "applied")
    assert applied["confirmation_source"] == "simulated"


def test_due_rules_are_enqueued_once(client, db_session, auth_headers, context):
    rule = create_rule(client, auth_headers, "daily_summary")
    db_session.execute(
        update(AutomationRule)
        .where(AutomationRule.id == rule["id"])
        .values(next_run_at=datetime.now(UTC) - timedelta(minutes=1))
    )

    first = tick(db_session, context)
    second = tick(db_session, context)

    jobs = db_session.scalars(select(ScheduledJob)).all()
    assert first.enqueued == 1 and second.enqueued == 0
    assert len(jobs) == 1 and jobs[0].status is JobRunStatus.SUCCEEDED
    assert jobs[0].logs[0].summary["awaiting_approval"] == 0


# --- Reserva, retentativas e recuperação ------------------------------------------------


def pending_job(client, db_session, headers) -> ScheduledJob:
    rule = create_rule(client, headers, "daily_summary")
    job_id = run_now(client, headers, rule["id"])["id"]
    return db_session.get(ScheduledJob, job_id)


def test_claimed_job_is_not_claimed_twice_until_lease_expires(client, db_session, auth_headers):
    pending_job(client, db_session, auth_headers)
    now = datetime.now(UTC)

    first = runner.claim_next_job(db_session, "worker-a", now, LEASE)
    while_running = runner.claim_next_job(db_session, "worker-b", now, LEASE)
    after_lease = runner.claim_next_job(db_session, "worker-b", now + LEASE * 2, LEASE)

    assert first is not None and while_running is None
    assert after_lease.id == first.id
    assert after_lease.attempts == 2 and after_lease.locked_by == "worker-b"


def test_integration_failure_is_retried_with_backoff(
    client, db_session, auth_headers, context, monkeypatch
):
    def failing(*args):
        raise IntegrationError("provedor fora do ar")

    monkeypatch.setitem(tasks.TASK_HANDLERS, tasks.AutomationTask.DAILY_SUMMARY, failing)
    job = pending_job(client, db_session, auth_headers)
    now = datetime.now(UTC)

    tick(db_session, context, now=now)
    db_session.refresh(job)
    assert job.status is JobRunStatus.PENDING
    assert job.next_attempt_at == now + runner.RETRY_BASE_DELAY

    tick(db_session, context, now=now + timedelta(minutes=2))
    tick(db_session, context, now=now + timedelta(minutes=10))
    db_session.refresh(job)
    assert job.status is JobRunStatus.FAILED
    assert job.attempts == 3
    assert [log.status for log in job.logs] == [JobRunStatus.FAILED] * 3


def test_domain_error_is_not_retried(client, db_session, auth_headers, context, monkeypatch):
    def misconfigured(*args):
        raise ValidationError("Nenhuma fonte de vagas habilitada.")

    monkeypatch.setitem(tasks.TASK_HANDLERS, tasks.AutomationTask.DAILY_SUMMARY, misconfigured)
    job = pending_job(client, db_session, auth_headers)

    tick(db_session, context)

    db_session.refresh(job)
    assert job.status is JobRunStatus.FAILED and job.attempts == 1
    assert job.last_error == "Nenhuma fonte de vagas habilitada."


def test_stale_applying_submission_is_recovered_as_uncertain(
    client, db_session, auth_headers, context
):
    jobs = setup_jobs(client, auth_headers)
    created = client.post(
        "/api/v1/applications", json={"job_id": jobs["mock-001"]}, headers=auth_headers
    ).json()
    db_session.execute(
        update(Application)
        .where(Application.id == created["id"])
        .values(
            status=ApplicationStatus.APPLYING,
            updated_at=datetime.now(UTC) - timedelta(hours=1),
        )
    )

    result = tick(db_session, context)

    application = client.get(f"/api/v1/applications/{created['id']}", headers=auth_headers).json()
    events = client.get(f"/api/v1/applications/{created['id']}/events", headers=auth_headers)
    assert result.recovered_submissions == 1
    assert application["status"] == "error"
    assert events.json()[-1]["details"] == {"outcome_unknown": True}
