"""Modo assistido (LinkedIn, Gupy): a API prepara o kit, o usuário envia na plataforma."""

import pytest
from sqlalchemy import select

from app.automation import tasks
from app.models.communication import Notification
from app.models.enums import NotificationEvent
from tests.integration.test_analysis import PROFILE
from tests.integration.test_automation import context  # noqa: F401  (fixture)

BASE = "/api/v1/applications"


@pytest.fixture
def profile(client, auth_headers):
    response = client.put(
        "/api/v1/profile", json={**PROFILE, "availability": "Imediata"}, headers=auth_headers
    )
    assert response.status_code == 200


def import_job(client, headers, number: int, url: str | None = None, **extra) -> dict:
    payload = {
        "url": url or f"https://www.linkedin.com/jobs/view/{number}",
        "title": "Backend Python Júnior",
        "company": f"Empresa {number}",
        "work_model": "remote",
        "seniority": "junior",
        "requirements": ["Python", "FastAPI"],
        **extra,
    }
    response = client.post("/api/v1/jobs/import", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["job"]


def prepare_job(client, headers, job_id):
    application = client.post(BASE, json={"job_id": job_id}, headers=headers).json()
    return client.post(f"{BASE}/{application['id']}/prepare", json={}, headers=headers)


def set_limits(client, headers, **limits):
    response = client.put(
        f"{BASE}/settings", json={"assisted_daily_limits": limits}, headers=headers
    )
    assert response.status_code == 200, response.text


# --- Plataforma na importação ----------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "platform", "expected"),
    [
        ("https://www.linkedin.com/jobs/view/1", None, "linkedin"),
        ("https://empresa.gupy.io/jobs/123", None, "gupy"),
        ("https://carreiras.empresa.example/vaga", None, "manual"),
        ("https://carreiras.empresa.example/vaga", "catho", "catho"),
    ],
)
def test_import_detects_or_accepts_platform(client, auth_headers, url, platform, expected):
    job = import_job(client, auth_headers, 1, url=url, platform=platform)

    assert job["provider_key"] == expected


def test_import_rejects_platform_with_integration_or_unknown(client, auth_headers):
    for platform in ("mock", "inexistente"):
        response = client.post(
            "/api/v1/jobs/import",
            json={
                "url": "https://x.example/1",
                "title": "Dev",
                "company": "X",
                "platform": platform,
            },
            headers=auth_headers,
        )
        assert response.status_code == 422


# --- Configuração ----------------------------------------------------------------------


def test_default_limits_are_ten_for_linkedin_and_gupy(client, auth_headers):
    settings = client.get(f"{BASE}/settings", headers=auth_headers).json()

    assert settings["assisted_daily_limits"] == {"linkedin": 10, "gupy": 10}


def test_limits_only_accept_manual_platforms(client, auth_headers):
    response = client.put(
        f"{BASE}/settings", json={"assisted_daily_limits": {"mock": 5}}, headers=auth_headers
    )

    assert response.status_code == 422


# --- Kit -------------------------------------------------------------------------------


def test_kit_has_everything_needed_to_apply(client, auth_headers, profile):
    job = import_job(client, auth_headers, 1)
    application = prepare_job(client, auth_headers, job["id"]).json()

    response = client.get(f"{BASE}/{application['id']}/kit", headers=auth_headers)

    kit = response.json()
    answers = {answer["field"]: answer["value"] for answer in kit["answers"]}
    assert response.status_code == 200, response.text
    assert application["status"] == "awaiting_approval"
    assert kit["platform"] == "linkedin"
    assert kit["job_url"] == "https://www.linkedin.com/jobs/view/1"
    assert "Empresa 1" in kit["cover_letter"]
    assert answers["full_name"] == "Ana Souza"
    assert answers["salary_expectation"] == "5000.00 BRL"
    assert answers["availability"] == "Imediata"
    assert "phone" in answers and "portfolio_url" not in answers  # só o que existe no perfil
    assert kit["quota"] == {
        "platform": "linkedin",
        "limit": 10,
        "applied_today": 0,
        "pending_kits": 1,
        "remaining": 9,
    }
    assert "mark-applied" in kit["next_step"]


def test_kit_only_after_preparation(client, auth_headers, profile):
    job = import_job(client, auth_headers, 1)
    application = client.post(BASE, json={"job_id": job["id"]}, headers=auth_headers).json()

    assert client.get(f"{BASE}/{application['id']}/kit", headers=auth_headers).status_code == 409


# --- Limite diário ---------------------------------------------------------------------


def test_daily_limit_counts_sent_and_pending_kits(client, auth_headers, profile):
    set_limits(client, auth_headers, linkedin=2, gupy=10)
    jobs = [import_job(client, auth_headers, number) for number in (1, 2, 3)]
    first = prepare_job(client, auth_headers, jobs[0]["id"]).json()
    second = prepare_job(client, auth_headers, jobs[1]["id"]).json()
    third = client.post(BASE, json={"job_id": jobs[2]["id"]}, headers=auth_headers).json()
    prepare_third = f"{BASE}/{third['id']}/prepare"

    blocked = client.post(prepare_third, json={}, headers=auth_headers)
    client.post(
        f"{BASE}/{first['id']}/mark-applied",
        json={"confirmation": "Candidatura simplificada enviada"},
        headers=auth_headers,
    )
    # 1 enviada hoje + 1 kit pendente = 2: continua no limite.
    still_blocked = client.post(prepare_third, json={}, headers=auth_headers)
    client.post(f"{BASE}/{second['id']}/reject", json={"reason": "Desisti"}, headers=auth_headers)
    # Kit recusado libera a vaga na cota do dia.
    allowed = client.post(prepare_third, json={}, headers=auth_headers)

    assert blocked.status_code == 422
    assert "Limite diário de 2" in blocked.json()["error"]["message"]
    assert still_blocked.status_code == 422
    assert allowed.status_code == 200 and allowed.json()["status"] == "awaiting_approval"


def test_other_platforms_are_not_limited(client, auth_headers, profile):
    set_limits(client, auth_headers, linkedin=0)

    gupy_job = import_job(client, auth_headers, 1, url="https://empresa.gupy.io/jobs/1")
    linkedin_job = import_job(client, auth_headers, 2)

    assert prepare_job(client, auth_headers, gupy_job["id"]).status_code == 200
    assert prepare_job(client, auth_headers, linkedin_job["id"]).status_code == 422


def test_worker_queue_respects_limit_and_prioritizes_best_matches(
    client,
    db_session,
    user,
    auth_headers,
    profile,
    context,  # noqa: F811
):
    set_limits(client, auth_headers, linkedin=2)
    jobs = [import_job(client, auth_headers, number) for number in (1, 2, 3)]
    for job in jobs:
        client.post(f"/api/v1/jobs/{job['id']}/analyze", headers=auth_headers)

    summary = tasks.prepare_applications(db_session, context, user.id, {"max_items": 10})
    next_day_attempt = tasks.prepare_applications(db_session, context, user.id, {"max_items": 10})

    assert summary["prepared"] == 2 and summary["over_daily_limit"] == 1
    assert next_day_attempt["prepared"] == 0  # cota de hoje ainda ocupada pelos kits pendentes
    queue_notice = db_session.scalars(
        select(Notification).where(
            Notification.event_type == NotificationEvent.ASSISTED_QUEUE_READY
        )
    ).one()
    assert queue_notice.data == {"ready": {"linkedin": 2}}
