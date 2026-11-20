from datetime import UTC, datetime, timedelta

import pytest

from tests.conftest import create_test_user, login
from tests.integration.test_applications import ready_application

BASE = "/api/v1/dashboard"


@pytest.fixture
def scenario(client, auth_headers):
    """Cenário real criado pela própria API:
    - 7 vagas do provedor simulado (8 anúncios, 1 duplicado) + 1 importada manualmente;
    - mock-001 analisada (qualificada), enviada e em entrevista;
    - mock-002 analisada e descartada (critério eliminatório).
    """
    application = ready_application(client, auth_headers)
    client.post(f"/api/v1/applications/{application['id']}/submit", headers=auth_headers)
    client.post(
        f"/api/v1/applications/{application['id']}/progress",
        json={"status": "interview"},
        headers=auth_headers,
    )
    jobs = client.get("/api/v1/jobs", params={"limit": 100}, headers=auth_headers).json()["items"]
    mock_002 = next(job for job in jobs if job["company"] == "Nuvem Pay")
    rejected = client.post(f"/api/v1/jobs/{mock_002['id']}/analyze", headers=auth_headers).json()
    client.post(
        "/api/v1/jobs/import",
        json={"url": "https://manual.example/1", "title": "Dev Python", "company": "Manual SA"},
        headers=auth_headers,
    )
    qualified_score = client.get(
        f"/api/v1/applications/{application['id']}", headers=auth_headers
    ).json()["compatibility_score"]
    return {"scores": [qualified_score, rejected["score"]]}


def get(client, headers, path, **params):
    response = client.get(f"{BASE}/{path}", params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_overview_is_computed_from_real_records(client, auth_headers, scenario):
    body = get(client, auth_headers, "overview")

    assert body["jobs_found"] == 8
    assert body["jobs_analyzed"] == 2
    assert body["jobs_qualified"] == 1 and body["jobs_rejected"] == 1
    assert body["average_score"] == round(sum(scenario["scores"]) / 2, 1)
    assert body["applications_submitted"] == 1
    assert body["interviews"] == 1 and body["offers"] == 0
    assert body["interview_rate"] == 1.0
    assert body["applications_awaiting_approval"] == 0


def test_funnel_stages_and_rates(client, auth_headers, scenario):
    stages = {stage["stage"]: stage for stage in get(client, auth_headers, "funnel")["stages"]}

    assert stages["jobs_found"]["count"] == 8
    assert stages["jobs_found"]["rate_from_previous"] is None
    assert stages["jobs_analyzed"]["rate_from_previous"] == 0.25
    assert stages["applications_submitted"]["count"] == 1
    assert stages["interview"]["count"] == 1
    assert stages["hired"]["count"] == 0


def test_platform_breakdown(client, auth_headers, scenario):
    platforms = {item["provider_key"]: item for item in get(client, auth_headers, "platforms")}

    assert platforms["mock"]["jobs_found"] == 7
    assert platforms["mock"]["jobs_qualified"] == 1
    assert platforms["mock"]["applications_submitted"] == 1
    assert platforms["mock"]["interviews"] == 1
    assert platforms["manual"]["jobs_found"] == 1
    assert platforms["manual"]["average_score"] is None


def test_timeline_groups_by_period(client, auth_headers, scenario):
    body = get(client, auth_headers, "timeline", period="day")

    assert len(body["points"]) == 1
    point = body["points"][0]
    assert point["jobs_found"] == 8
    assert point["applications_submitted"] == 1
    assert point["interviews"] == 1


@pytest.mark.parametrize(
    ("params", "expected_jobs"),
    [
        ({"provider_key": "manual"}, 1),
        ({"company": "acme"}, 2),
        ({"title": "embarcados"}, 1),
        ({"min_score": 70}, 1),
    ],
)
def test_filters(client, auth_headers, scenario, params, expected_jobs):
    assert get(client, auth_headers, "overview", **params)["jobs_found"] == expected_jobs


def test_status_filter_applies_to_application_metrics(client, auth_headers, scenario):
    body = get(client, auth_headers, "overview", status="closed")

    assert body["applications_submitted"] == 0
    assert body["jobs_found"] == 8  # status filtra candidaturas, não vagas


def test_date_range_outside_activity_is_empty(client, auth_headers, scenario):
    future = datetime.now(UTC) + timedelta(days=10)

    body = get(
        client,
        auth_headers,
        "overview",
        date_from=future.isoformat(),
        date_to=(future + timedelta(days=1)).isoformat(),
    )

    assert body["jobs_found"] == 0 and body["interview_rate"] is None


@pytest.mark.parametrize(
    "params",
    [
        {"date_from": "2026-02-01T00:00:00Z", "date_to": "2026-01-01T00:00:00Z"},
        {"date_from": "2024-01-01T00:00:00Z", "date_to": "2026-01-01T00:00:00Z"},
        {"date_from": "2026-01-01T00:00:00"},  # sem fuso horário
        {"min_score": 101},
    ],
)
def test_invalid_filters(client, auth_headers, params):
    response = client.get(f"{BASE}/overview", params=params, headers=auth_headers)

    assert response.status_code == 422


def test_metrics_are_isolated_per_user(client, db_session, auth_headers, scenario):
    other = create_test_user(db_session, "other@example.com")
    headers = {"Authorization": f"Bearer {login(client, other.email)['access_token']}"}

    body = get(client, headers, "overview")

    assert body["jobs_found"] == 0 and body["applications_submitted"] == 0
    assert get(client, headers, "platforms") == []
