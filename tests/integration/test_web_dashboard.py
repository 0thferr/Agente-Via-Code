"""Dashboard web: arquivos servidos pela API, cabeçalhos de segurança e dados de apoio."""

from tests.integration.test_analysis import setup_jobs


def test_root_redirects_to_dashboard(client):
    response = client.get("/", follow_redirects=False)

    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/app/"


def test_dashboard_page_has_security_headers(client):
    response = client.get("/app/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    csp = response.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_dashboard_scripts_are_served(client):
    paths = [
        "/app/js/app.js",
        "/app/js/api.js",
        "/app/js/ui.js",
        "/app/js/forms.js",
        "/app/styles.css",
        *(
            f"/app/js/views/{name}.js"
            for name in (
                "login",
                "overview",
                "jobs",
                "applications",
                "notifications",
                "profile",
                "settings",
                "automations",
            )
        ),
    ]
    for path in paths:
        assert client.get(path).status_code == 200, path


def test_api_responses_do_not_get_page_csp(client):
    assert "content-security-policy" not in client.get("/api/v1/health").headers


def test_job_list_includes_latest_analysis(client, auth_headers):
    jobs = setup_jobs(client, auth_headers)
    client.post(f"/api/v1/jobs/{jobs['mock-001']}/analyze", headers=auth_headers)

    items = client.get("/api/v1/jobs", params={"limit": 100}, headers=auth_headers).json()["items"]

    by_id = {item["id"]: item for item in items}
    assert by_id[jobs["mock-001"]]["latest_score"] == 76
    assert by_id[jobs["mock-001"]]["latest_recommendation"] == "qualified"
    assert by_id[jobs["mock-002"]]["latest_score"] is None


def test_prepare_explains_the_real_reason_when_analysis_is_pending(client, auth_headers):
    from tests.integration.test_analysis import PROFILE

    client.put("/api/v1/profile", json=PROFILE, headers=auth_headers)
    # Descrição longa, mas sem requisitos listados: o analisador simulado não tem o que avaliar.
    job = client.post(
        "/api/v1/jobs/import",
        json={
            "url": "https://www.linkedin.com/jobs/view/7",
            "title": "Dev Python",
            "company": "Empresa",
            "work_model": "remote",
            "description": "Buscamos pessoa desenvolvedora para APIs. " * 10,
        },
        headers=auth_headers,
    ).json()["job"]
    application = client.post(
        "/api/v1/applications", json={"job_id": job["id"]}, headers=auth_headers
    )

    response = client.post(
        f"/api/v1/applications/{application.json()['id']}/prepare", json={}, headers=auth_headers
    )

    message = response.json()["error"]["message"]
    assert response.status_code == 422
    assert "Nenhum requisito avaliável" in message
    assert "descrição da vaga é insuficiente" not in message
