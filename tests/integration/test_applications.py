import pytest

from app.api import deps
from app.integrations.application_submitters.base import (
    SubmissionError,
    SubmissionOutcomeUnknownError,
)
from app.integrations.application_submitters.mock import MockApplicationSubmitter
from app.integrations.application_submitters.registry import SubmitterRegistry
from app.main import app
from tests.conftest import create_test_user, login
from tests.integration.test_analysis import PROFILE, setup_jobs
from tests.integration.test_resume_import import RESUME_LINES, upload
from tests.pdf_factory import make_pdf

BASE = "/api/v1/applications"


class FlakySubmitter(MockApplicationSubmitter):
    """Falha na primeira tentativa e registra as chaves de idempotência recebidas."""

    def __init__(self, first_error: Exception) -> None:
        super().__init__()
        self.first_error = first_error
        self.keys: list[str] = []

    def submit(self, payload, idempotency_key):
        self.keys.append(idempotency_key)
        if len(self.keys) == 1:
            raise self.first_error
        return super().submit(payload, idempotency_key)


@pytest.fixture
def submitter():
    instance = MockApplicationSubmitter()
    use_submitter(instance)
    return instance


def use_submitter(instance) -> None:
    app.dependency_overrides[deps.get_submitter_registry] = lambda: SubmitterRegistry([instance])


def set_settings(client, headers, **settings):
    response = client.put(f"{BASE}/settings", json=settings, headers=headers)
    assert response.status_code == 200, response.text


def create(client, headers, job_id) -> dict:
    return client.post(BASE, json={"job_id": job_id}, headers=headers).json()


def prepare(client, headers, application_id, **payload):
    return client.post(f"{BASE}/{application_id}/prepare", json=payload, headers=headers)


def ready_application(client, headers, external_id="mock-001") -> dict:
    """Candidatura preparada, com currículo, perguntas respondidas e aprovada."""
    jobs = setup_jobs(client, headers)
    upload(client, headers, make_pdf(RESUME_LINES))
    application = create(client, headers, jobs[external_id])
    prepare(client, headers, application["id"])
    client.post(
        f"{BASE}/{application['id']}/answers",
        json={"answers": {"availability": "Imediata"}},
        headers=headers,
    )
    approved = client.post(f"{BASE}/{application['id']}/approve", json={}, headers=headers)
    assert approved.status_code == 200, approved.text
    return approved.json()


def events(client, headers, application_id) -> list[dict]:
    return client.get(f"{BASE}/{application_id}/events", headers=headers).json()


# --- Criação e preparação --------------------------------------------------------------


def test_create_is_idempotent_one_application_per_job(client, auth_headers):
    jobs = setup_jobs(client, auth_headers)

    first = client.post(BASE, json={"job_id": jobs["mock-001"]}, headers=auth_headers)
    second = client.post(BASE, json={"job_id": jobs["mock-001"]}, headers=auth_headers)

    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["status"] == "discovered"


def test_prepare_qualified_job_awaits_approval_with_human_intervention(
    client, auth_headers, submitter
):
    jobs = setup_jobs(client, auth_headers)
    application = create(client, auth_headers, jobs["mock-001"])

    response = prepare(client, auth_headers, application["id"])

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["status"] == "awaiting_approval"
    assert body["compatibility_score"] == 76
    assert body["cover_letter_id"] is not None
    assert "Python" in body["resume_highlights"]
    # O perfil não tem "disponibilidade": a resposta não é inventada, vira pendência.
    assert body["pending_questions"] == [
        {"id": "availability", "label": "Disponibilidade para início"}
    ]
    statuses = [event["to_status"] for event in events(client, auth_headers, body["id"])]
    assert statuses == ["discovered", "analyzing", "qualified", "awaiting_approval"]


def test_prepare_rejected_job(client, auth_headers):
    jobs = setup_jobs(client, auth_headers)
    application = create(client, auth_headers, jobs["mock-002"])

    body = prepare(client, auth_headers, application["id"]).json()

    assert body["status"] == "rejected"
    assert "eliminatório" in body["compatibility_summary"]
    assert body["cover_letter_id"] is None


def test_prepare_twice_is_rejected(client, auth_headers):
    jobs = setup_jobs(client, auth_headers)
    application = create(client, auth_headers, jobs["mock-001"])
    prepare(client, auth_headers, application["id"])

    assert prepare(client, auth_headers, application["id"]).status_code == 409


# --- Perguntas e aprovação -------------------------------------------------------------


def test_approval_blocked_until_questions_are_answered(client, auth_headers, submitter):
    jobs = setup_jobs(client, auth_headers)
    upload(client, auth_headers, make_pdf(RESUME_LINES))
    application = create(client, auth_headers, jobs["mock-001"])
    prepare(client, auth_headers, application["id"])
    url = f"{BASE}/{application['id']}"

    blocked = client.post(f"{url}/approve", json={}, headers=auth_headers)
    unknown = client.post(f"{url}/answers", json={"answers": {"cnh": "sim"}}, headers=auth_headers)
    answered = client.post(
        f"{url}/answers", json={"answers": {"availability": "Imediata"}}, headers=auth_headers
    )
    approved = client.post(f"{url}/approve", json={}, headers=auth_headers)

    assert blocked.status_code == 422
    assert unknown.status_code == 422
    assert answered.json()["pending_questions"] == []
    assert answered.json()["answered_questions"] == ["availability"]
    assert approved.json()["status"] == "ready_to_apply"
    # A timeline registra quais perguntas foram respondidas, nunca o conteúdo.
    assert "Imediata" not in str(events(client, auth_headers, application["id"]))


def test_approval_requires_resume_when_platform_needs_it(client, auth_headers, submitter):
    jobs = setup_jobs(client, auth_headers)
    application = create(client, auth_headers, jobs["mock-001"])
    prepare(client, auth_headers, application["id"])
    client.post(
        f"{BASE}/{application['id']}/answers",
        json={"answers": {"availability": "Imediata"}},
        headers=auth_headers,
    )

    response = client.post(f"{BASE}/{application['id']}/approve", json={}, headers=auth_headers)

    assert response.status_code == 422
    assert "currículo" in response.json()["error"]["message"]


def test_approving_text_with_warnings_requires_acknowledgement(client, auth_headers, submitter):
    jobs = setup_jobs(client, auth_headers)
    upload(client, auth_headers, make_pdf(RESUME_LINES))
    application = create(client, auth_headers, jobs["mock-001"])
    prepared = prepare(client, auth_headers, application["id"]).json()
    client.post(
        f"{BASE}/{application['id']}/answers",
        json={"answers": {"availability": "Imediata"}},
        headers=auth_headers,
    )
    revision = client.post(
        f"/api/v1/cover-letters/{prepared['cover_letter_id']}/revisions",
        json={"content": "Tenho experiência com Docker e Python."},
        headers=auth_headers,
    ).json()
    url = f"{BASE}/{application['id']}/approve"

    without_ack = client.post(url, json={"cover_letter_id": revision["id"]}, headers=auth_headers)
    with_ack = client.post(
        url,
        json={"cover_letter_id": revision["id"], "acknowledge_warnings": True},
        headers=auth_headers,
    )

    assert without_ack.status_code == 422
    assert with_ack.status_code == 200
    assert with_ack.json()["cover_letter_id"] == revision["id"]


# --- Envio -----------------------------------------------------------------------------


def test_submit_requires_approval(client, auth_headers, submitter):
    jobs = setup_jobs(client, auth_headers)
    application = create(client, auth_headers, jobs["mock-001"])
    prepare(client, auth_headers, application["id"])

    response = client.post(f"{BASE}/{application['id']}/submit", headers=auth_headers)

    assert response.status_code == 409
    assert submitter.calls == 0


def test_submit_after_approval_is_confirmed_and_idempotent(client, auth_headers, submitter):
    application = ready_application(client, auth_headers)
    url = f"{BASE}/{application['id']}/submit"

    first = client.post(url, headers=auth_headers)
    second = client.post(url, headers=auth_headers)

    body = first.json()
    assert first.status_code == 200, first.text
    assert body["status"] == "applied"
    assert body["confirmation_source"] == "simulated"
    assert body["external_confirmation_id"].startswith("SIM-")
    assert second.json()["external_confirmation_id"] == body["external_confirmation_id"]
    assert submitter.calls == 1


def test_uncertain_submission_is_never_counted_as_sent_and_retry_reuses_key(client, auth_headers):
    flaky = FlakySubmitter(SubmissionOutcomeUnknownError("timeout após enviar"))
    use_submitter(flaky)
    application = ready_application(client, auth_headers)
    url = f"{BASE}/{application['id']}/submit"

    failed = client.post(url, headers=auth_headers)
    state_after_failure = client.get(f"{BASE}/{application['id']}", headers=auth_headers).json()
    retried = client.post(url, headers=auth_headers)

    assert failed.status_code == 502
    assert state_after_failure["status"] == "error"
    assert state_after_failure["external_confirmation_id"] is None
    assert retried.json()["status"] == "applied"
    assert flaky.keys[0] == flaky.keys[1]  # mesma chave: a plataforma deduplica


def test_rejected_submission_goes_to_error(client, auth_headers):
    use_submitter(FlakySubmitter(SubmissionError("vaga encerrada")))
    application = ready_application(client, auth_headers)

    response = client.post(f"{BASE}/{application['id']}/submit", headers=auth_headers)
    current = client.get(f"{BASE}/{application['id']}", headers=auth_headers).json()

    assert response.status_code == 502
    assert current["status"] == "error"
    assert current["last_error"] == "vaga encerrada"


def test_daily_limit_blocks_submission(client, auth_headers, submitter):
    application = ready_application(client, auth_headers)
    set_settings(client, auth_headers, max_daily_submissions=0)

    response = client.post(f"{BASE}/{application['id']}/submit", headers=auth_headers)

    assert response.status_code == 422
    assert submitter.calls == 0


# --- Modos e fluxo manual --------------------------------------------------------------


def test_manual_mode_never_submits_and_accepts_user_declaration(client, auth_headers, submitter):
    set_settings(client, auth_headers, mode="manual")
    application = ready_application(client, auth_headers)
    url = f"{BASE}/{application['id']}"

    submit = client.post(f"{url}/submit", headers=auth_headers)
    marked = client.post(
        f"{url}/mark-applied", json={"confirmation": "Protocolo 12345"}, headers=auth_headers
    )

    assert submit.status_code == 422
    assert submitter.calls == 0
    assert marked.json()["status"] == "applied"
    assert marked.json()["confirmation_source"] == "user_declared"


def test_platform_without_integration_uses_manual_flow(client, auth_headers):
    setup_jobs(client, auth_headers)
    job = client.post(
        "/api/v1/jobs/import",
        json={
            "url": "https://linkedin.example/jobs/1",
            "title": "Backend Python Júnior",
            "company": "Empresa LinkedIn",
            "work_model": "remote",
            "requirements": ["Python", "FastAPI"],
        },
        headers=auth_headers,
    ).json()["job"]
    application = create(client, auth_headers, job["id"])
    prepared = prepare(client, auth_headers, application["id"]).json()
    url = f"{BASE}/{application['id']}"

    approved = client.post(f"{url}/approve", json={}, headers=auth_headers)
    submit = client.post(f"{url}/submit", headers=auth_headers)

    assert prepared["status"] == "awaiting_approval"
    assert prepared["pending_questions"] == []
    assert approved.status_code == 200
    assert submit.status_code == 422
    assert "mark-applied" in submit.json()["error"]["message"]


def test_automatic_mode_submits_without_approval_only_when_rules_allow(
    client, auth_headers, submitter
):
    set_settings(client, auth_headers, mode="automatic", auto_submit_providers=["mock"])
    jobs = setup_jobs(client, auth_headers)
    client.put(
        "/api/v1/profile", json={**PROFILE, "availability": "Imediata"}, headers=auth_headers
    )
    upload(client, auth_headers, make_pdf(RESUME_LINES))
    application = create(client, auth_headers, jobs["mock-001"])

    prepared = prepare(client, auth_headers, application["id"]).json()
    submitted = client.post(f"{BASE}/{application['id']}/submit", headers=auth_headers)

    assert prepared["status"] == "ready_to_apply"
    assert "auto_approved" in [
        e["event_type"] for e in events(client, auth_headers, prepared["id"])
    ]
    assert submitted.json()["status"] == "applied"


def test_automatic_mode_without_allowlist_requires_approval(client, auth_headers, submitter):
    set_settings(client, auth_headers, mode="automatic", auto_submit_providers=[])
    jobs = setup_jobs(client, auth_headers)
    application = create(client, auth_headers, jobs["mock-001"])

    prepared = prepare(client, auth_headers, application["id"]).json()

    reasons = next(
        e["details"]["approval_required_because"]
        for e in events(client, auth_headers, prepared["id"])
        if e["to_status"] == "awaiting_approval"
    )
    assert prepared["status"] == "awaiting_approval"
    assert any("lista de envio automático" in reason for reason in reasons)


# --- Acompanhamento e encerramento -----------------------------------------------------


def test_progress_after_submission(client, auth_headers, submitter):
    application = ready_application(client, auth_headers)
    client.post(f"{BASE}/{application['id']}/submit", headers=auth_headers)
    url = f"{BASE}/{application['id']}/progress"

    skip_to_hired = client.post(url, json={"status": "hired"}, headers=auth_headers)
    interview = client.post(
        url, json={"status": "interview", "note": "Entrevista técnica"}, headers=auth_headers
    )

    assert skip_to_hired.status_code == 409
    assert interview.json()["status"] == "interview"


def test_decline_closes_application(client, auth_headers):
    jobs = setup_jobs(client, auth_headers)
    application = create(client, auth_headers, jobs["mock-001"])

    declined = client.post(
        f"{BASE}/{application['id']}/reject",
        json={"reason": "Não tenho interesse"},
        headers=auth_headers,
    )

    assert declined.json()["status"] == "closed"
    assert prepare(client, auth_headers, application["id"]).status_code == 409


def test_list_filters_by_status(client, auth_headers):
    jobs = setup_jobs(client, auth_headers)
    create(client, auth_headers, jobs["mock-001"])
    rejected = create(client, auth_headers, jobs["mock-002"])
    prepare(client, auth_headers, rejected["id"])

    response = client.get(BASE, params={"status": "rejected"}, headers=auth_headers).json()

    assert response["total"] == 1
    assert response["items"][0]["id"] == rejected["id"]


def test_applications_are_private(client, db_session, auth_headers):
    jobs = setup_jobs(client, auth_headers)
    application = create(client, auth_headers, jobs["mock-001"])
    other = create_test_user(db_session, "other@example.com")
    other_headers = {"Authorization": f"Bearer {login(client, other.email)['access_token']}"}

    assert client.get(f"{BASE}/{application['id']}", headers=other_headers).status_code == 404
    assert (
        client.post(BASE, json={"job_id": jobs["mock-001"]}, headers=other_headers).status_code
        == 404
    )
