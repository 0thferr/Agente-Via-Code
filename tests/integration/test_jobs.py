from sqlalchemy import func, select

from app.api import deps
from app.integrations.job_providers.base import (
    IntegrationStatus,
    JobProvider,
    JobSearchQuery,
    ProviderCapability,
    ProviderError,
    ProviderInfo,
    RawJob,
)
from app.integrations.job_providers.mock import MockJobProvider
from app.integrations.job_providers.registry import ProviderRegistry
from app.main import app
from app.models.job import JobOpportunity
from tests.conftest import create_test_user, login

MANUAL_JOB = {
    "url": "https://empresa.example/carreiras/vaga-42?utm_source=linkedin",
    "title": "Desenvolvedora Python",
    "company": "Empresa Exemplo",
    "description": "<p>APIs com <b>FastAPI</b></p>",
    "location": "Recife, PE",
    "salary_min": "5000",
    "salary_max": "7000",
    "salary_currency": "BRL",
}


class FailingProvider(JobProvider):
    info = ProviderInfo(
        key="failing",
        name="Fonte instável",
        integration_status=IntegrationStatus.AUTOMATED,
        capabilities=frozenset({ProviderCapability.SEARCH}),
    )

    def search(self, query: JobSearchQuery) -> list[RawJob]:
        raise ProviderError("timeout ao consultar a fonte")

    def get_details(self, external_id: str) -> RawJob | None:
        return None


def use_registry(*providers: JobProvider) -> None:
    app.dependency_overrides[deps.get_provider_registry] = lambda: ProviderRegistry(list(providers))


def create_source(client, headers, provider_key: str = "mock") -> str:
    response = client.post(
        "/api/v1/jobs/sources", json={"provider_key": provider_key}, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def count_jobs(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(JobOpportunity))


def test_providers_catalog_exposes_capabilities_and_status(client, auth_headers):
    response = client.get("/api/v1/jobs/providers", headers=auth_headers)
    providers = {item["key"]: item for item in response.json()}

    assert providers["mock"]["integration_status"] == "simulated"
    assert "search" in providers["mock"]["capabilities"]
    assert providers["linkedin"]["integration_status"] == "manual_only"
    assert providers["linkedin"]["capabilities"] == []


def test_manual_only_provider_cannot_be_a_search_source(client, auth_headers):
    response = client.post(
        "/api/v1/jobs/sources", json={"provider_key": "linkedin"}, headers=auth_headers
    )

    assert response.status_code == 422


def test_unknown_provider_returns_404(client, auth_headers):
    response = client.post(
        "/api/v1/jobs/sources", json={"provider_key": "nope"}, headers=auth_headers
    )

    assert response.status_code == 404


def test_duplicated_source_is_rejected(client, auth_headers):
    create_source(client, auth_headers)
    response = client.post(
        "/api/v1/jobs/sources", json={"provider_key": "mock"}, headers=auth_headers
    )

    assert response.status_code == 409


def test_search_requires_enabled_source(client, auth_headers):
    assert client.post("/api/v1/jobs/search", json={}, headers=auth_headers).status_code == 422


def test_search_saves_jobs_and_deduplicates(client, db_session, auth_headers):
    create_source(client, auth_headers)

    response = client.post("/api/v1/jobs/search", json={}, headers=auth_headers)

    result = response.json()["results"][0]
    total_mock_jobs = len(MockJobProvider().search(JobSearchQuery()))
    assert response.status_code == 200, response.text
    assert result["fetched"] == total_mock_jobs
    # mock-008 é a mesma vaga do mock-001 com URL de rastreamento: vira duplicata.
    assert result["created"] == total_mock_jobs - 1
    assert result["duplicates"] == 1
    assert count_jobs(db_session) == total_mock_jobs - 1


def test_repeated_search_does_not_create_duplicates(client, db_session, auth_headers):
    create_source(client, auth_headers)
    client.post("/api/v1/jobs/search", json={}, headers=auth_headers)
    jobs_after_first = count_jobs(db_session)

    second = client.post("/api/v1/jobs/search", json={}, headers=auth_headers).json()

    assert second["results"][0]["created"] == 0
    assert second["created_job_ids"] == []
    assert count_jobs(db_session) == jobs_after_first


def test_search_applies_filters(client, auth_headers):
    create_source(client, auth_headers)

    response = client.post(
        "/api/v1/jobs/search",
        json={"filters": {"work_model": "onsite"}},
        headers=auth_headers,
    )
    jobs = client.get("/api/v1/jobs", headers=auth_headers).json()["items"]

    assert response.json()["results"][0]["created"] == len(jobs)
    assert {job["work_model"] for job in jobs} == {"onsite"}


def test_incremental_search_skips_already_synced_period(client, auth_headers):
    create_source(client, auth_headers)
    client.post("/api/v1/jobs/search", json={}, headers=auth_headers)

    response = client.post("/api/v1/jobs/search", json={"incremental": True}, headers=auth_headers)

    # As vagas simuladas são anteriores à primeira sincronização: nada é buscado de novo.
    assert response.json()["results"][0]["fetched"] == 0


def test_provider_failure_is_reported_without_breaking_other_sources(client, auth_headers):
    use_registry(MockJobProvider(), FailingProvider())
    create_source(client, auth_headers, "mock")
    create_source(client, auth_headers, "failing")

    response = client.post("/api/v1/jobs/search", json={}, headers=auth_headers)

    results = {item["provider_key"]: item for item in response.json()["results"]}
    assert response.status_code == 200
    assert results["failing"]["error"] == "timeout ao consultar a fonte"
    assert results["mock"]["created"] > 0


def test_invalid_jobs_from_provider_are_skipped(client, auth_headers):
    broken = RawJob(url="javascript:alert(1)", title="Vaga", company="X")
    valid = RawJob(url="https://ok.example/1", title="Vaga válida", company="Y")
    use_registry(MockJobProvider(jobs=[broken, valid]))
    create_source(client, auth_headers)

    result = client.post("/api/v1/jobs/search", json={}, headers=auth_headers).json()

    assert result["results"][0]["skipped"] == 1
    assert result["results"][0]["created"] == 1


def test_manual_import_normalizes_job(client, auth_headers):
    response = client.post("/api/v1/jobs/import", json=MANUAL_JOB, headers=auth_headers)

    job = response.json()["job"]
    assert response.status_code == 201, response.text
    assert response.json()["created"] is True
    assert job["provider_key"] == "manual"
    assert job["discovery_method"] == "manual"
    assert job["description"] == "APIs com  FastAPI"
    assert job["work_model"] is None  # não informado e não explícito no texto


def test_manual_import_is_idempotent(client, db_session, auth_headers):
    first = client.post("/api/v1/jobs/import", json=MANUAL_JOB, headers=auth_headers)
    same_url_other_tracking = {**MANUAL_JOB, "url": MANUAL_JOB["url"].split("?")[0] + "/"}
    second = client.post("/api/v1/jobs/import", json=same_url_other_tracking, headers=auth_headers)

    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["job"]["id"] == first.json()["job"]["id"]
    assert count_jobs(db_session) == 1


def test_same_job_from_another_url_is_detected_by_content(client, auth_headers):
    client.post("/api/v1/jobs/import", json=MANUAL_JOB, headers=auth_headers)
    other_platform = {**MANUAL_JOB, "url": "https://outra-plataforma.example/vagas/999"}

    response = client.post("/api/v1/jobs/import", json=other_platform, headers=auth_headers)

    assert response.json()["created"] is False


def test_manual_import_validation(client, auth_headers):
    invalid_payloads = [
        {**MANUAL_JOB, "url": "ftp://empresa.example/vaga"},
        {**MANUAL_JOB, "salary_min": "9000", "salary_max": "1000"},
        {**MANUAL_JOB, "salary_currency": None},
        {**MANUAL_JOB, "posted_at": "2026-11-01T10:00:00"},  # sem fuso horário
        {**MANUAL_JOB, "title": ""},
    ]
    for payload in invalid_payloads:
        response = client.post("/api/v1/jobs/import", json=payload, headers=auth_headers)
        assert response.status_code == 422, payload


def test_list_jobs_with_filters_and_pagination(client, auth_headers):
    create_source(client, auth_headers)
    client.post("/api/v1/jobs/search", json={}, headers=auth_headers)

    page = client.get("/api/v1/jobs", params={"limit": 2}, headers=auth_headers).json()
    by_company = client.get("/api/v1/jobs", params={"company": "acme"}, headers=auth_headers).json()
    by_text = client.get("/api/v1/jobs", params={"q": "embarcados"}, headers=auth_headers).json()

    assert len(page["items"]) == 2 and page["total"] > 2
    assert {job["company"] for job in by_company["items"]} == {"Acme Tech"}
    assert by_text["total"] == 1


def test_list_jobs_rejects_excessive_page_size(client, auth_headers):
    response = client.get("/api/v1/jobs", params={"limit": 1000}, headers=auth_headers)

    assert response.status_code == 422


def test_jobs_are_isolated_per_user(client, db_session, auth_headers):
    job_id = client.post("/api/v1/jobs/import", json=MANUAL_JOB, headers=auth_headers).json()[
        "job"
    ]["id"]
    other = create_test_user(db_session, "other@example.com")
    other_headers = {"Authorization": f"Bearer {login(client, other.email)['access_token']}"}

    assert client.get(f"/api/v1/jobs/{job_id}", headers=other_headers).status_code == 404
    assert client.get("/api/v1/jobs", headers=other_headers).json()["total"] == 0
    # O outro usuário pode importar a mesma vaga: a deduplicação é por usuário.
    other_import = client.post("/api/v1/jobs/import", json=MANUAL_JOB, headers=other_headers)
    assert other_import.status_code == 201


def test_disabled_source_is_not_searched(client, auth_headers):
    source_id = create_source(client, auth_headers)
    client.patch(
        f"/api/v1/jobs/sources/{source_id}", json={"is_enabled": False}, headers=auth_headers
    )

    response = client.post("/api/v1/jobs/search", json={}, headers=auth_headers)

    assert response.status_code == 422


def test_prompt_injection_text_is_stored_only_as_data(client, auth_headers):
    create_source(client, auth_headers)
    client.post("/api/v1/jobs/search", json={}, headers=auth_headers)

    jobs = client.get("/api/v1/jobs", params={"q": "Sênior"}, headers=auth_headers).json()
    detail = client.get(f"/api/v1/jobs/{jobs['items'][0]['id']}", headers=auth_headers).json()

    # O texto malicioso é preservado como conteúdo da vaga; nada no sistema o executa.
    assert "IGNORE TODAS AS INSTRUÇÕES" in detail["description"]
