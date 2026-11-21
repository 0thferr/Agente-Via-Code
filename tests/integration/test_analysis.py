from app.ai.llm import AIProviderError
from app.api import deps
from app.main import app
from app.models.enums import RequirementStatus
from app.services.analysis.requirements import (
    LLMRequirementAnalyzer,
    RequirementAnalysis,
    RequirementAssessment,
)
from tests.conftest import create_test_user, login
from tests.integration.test_profile import VALID_PROFILE

PROFILE = {**VALID_PROFILE, "seniority": "junior"}


class FakeLLM:
    """Simula um modelo de linguagem, registrando os prompts recebidos."""

    provider_name = "fake"
    model_name = "fake-model"

    def __init__(self, response=None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.prompts: list[str] = []

    def generate(self, *, system_prompt, user_prompt, output_model):
        self.prompts.append(user_prompt)
        if self.error:
            raise self.error
        return self.response


def setup_jobs(client, headers) -> dict[str, str]:
    """Cadastra perfil, habilita o provedor simulado e devolve {external_id: job_id}."""
    assert client.put("/api/v1/profile", json=PROFILE, headers=headers).status_code == 200
    client.post("/api/v1/jobs/sources", json={"provider_key": "mock"}, headers=headers)
    client.post("/api/v1/jobs/search", json={}, headers=headers)
    jobs = client.get("/api/v1/jobs", params={"limit": 100}, headers=headers).json()["items"]
    details = [client.get(f"/api/v1/jobs/{job['id']}", headers=headers).json() for job in jobs]
    return {detail["external_id"]: detail["id"] for detail in details}


def analyze(client, headers, job_id):
    return client.post(f"/api/v1/jobs/{job_id}/analyze", headers=headers)


def use_llm(llm) -> None:
    app.dependency_overrides[deps.get_requirement_analyzer] = lambda: LLMRequirementAnalyzer(llm)


def test_compatible_job_is_qualified_with_structured_result(client, auth_headers):
    jobs = setup_jobs(client, auth_headers)

    response = analyze(client, auth_headers, jobs["mock-001"])

    body = response.json()
    assert response.status_code == 200, response.text
    assert body["status"] == "completed"
    assert body["recommendation"] == "qualified"
    # skills 40 (2 de 4 obrigatórios, 0 de 2 desejáveis) e demais critérios 100:
    # (40*40 + 20*100 + 15*100 + 15*100 + 10*100) / 100 = 76
    assert body["score"] == 76
    assert set(body["unmet_mandatory_requirements"]) == {"SQL", "Git"}
    assert "Python" in body["matched_skills"]
    assert body["criteria"]["salary"]["fit"] == "match"
    assert body["analyzer_provider"] == "simulated"
    assert any("Nota 76 >= mínimo configurado 65" in r for r in body["decision_reasons"])


def test_eliminatory_rule_rejects_without_calling_ai(client, auth_headers):
    jobs = setup_jobs(client, auth_headers)
    llm = FakeLLM(error=AssertionError("a IA não deveria ser chamada"))
    use_llm(llm)

    body = analyze(client, auth_headers, jobs["mock-002"]).json()

    # Vaga híbrida no Rio de Janeiro; o perfil aceita só São Paulo.
    assert body["recommendation"] == "rejected"
    assert body["criteria"]["location"]["eliminatory"] is True
    assert body["analyzer_provider"] == "rules"
    assert llm.prompts == []


def test_insufficient_description_is_pending(client, auth_headers):
    setup_jobs(client, auth_headers)
    # Vaga remota que passa nos critérios eliminatórios, mas sem requisitos descritos.
    job = client.post(
        "/api/v1/jobs/import",
        json={
            "url": "https://vaga-curta.example/1",
            "title": "Desenvolvedora",
            "company": "Empresa Misteriosa",
            "description": "Mais detalhes na entrevista.",
            "work_model": "remote",
        },
        headers=auth_headers,
    ).json()["job"]

    body = analyze(client, auth_headers, job["id"]).json()

    assert body["status"] == "pending_information"
    assert body["recommendation"] == "pending"
    assert body["score"] is None


def test_prompt_injection_is_flagged_and_cannot_force_approval(client, auth_headers):
    jobs = setup_jobs(client, auth_headers)

    body = analyze(client, auth_headers, jobs["mock-007"]).json()

    assert any("prompt injection" in warning for warning in body["warnings"])
    assert body["recommendation"] == "rejected"
    assert body["confidence"] <= 0.5 * 0.7


def test_min_score_preference_changes_decision(client, auth_headers):
    jobs = setup_jobs(client, auth_headers)
    client.put("/api/v1/ai/preferences", json={"min_score": 80}, headers=auth_headers)

    body = analyze(client, auth_headers, jobs["mock-001"]).json()

    assert body["min_score"] == 80
    assert body["recommendation"] == "rejected"


def test_analysis_history_is_preserved(client, auth_headers):
    jobs = setup_jobs(client, auth_headers)
    analyze(client, auth_headers, jobs["mock-001"])
    analyze(client, auth_headers, jobs["mock-001"])

    history = client.get(f"/api/v1/jobs/{jobs['mock-001']}/analyses", headers=auth_headers)

    assert len(history.json()) == 2


def test_analysis_requires_profile(client, auth_headers):
    imported = client.post(
        "/api/v1/jobs/import",
        json={"url": "https://x.example/1", "title": "Dev", "company": "X"},
        headers=auth_headers,
    ).json()

    assert analyze(client, auth_headers, imported["job"]["id"]).status_code == 422


def test_llm_output_is_validated_against_profile(client, auth_headers):
    jobs = setup_jobs(client, auth_headers)
    llm = FakeLLM(
        RequirementAnalysis(
            requirements=[
                RequirementAssessment(
                    requirement="Python",
                    mandatory=True,
                    status=RequirementStatus.MET,
                    evidence_refs=["skill:1"],
                ),
                RequirementAssessment(
                    requirement="Kubernetes",
                    mandatory=True,
                    status=RequirementStatus.MET,
                    evidence_refs=["cert:7"],  # referência inventada
                ),
            ],
            summary="Boa aderência.",
            confidence=0.9,
        )
    )
    use_llm(llm)

    body = analyze(client, auth_headers, jobs["mock-001"]).json()

    statuses = {item["requirement"]: item["status"] for item in body["requirements"]}
    assert statuses["Kubernetes"] == "unclear"
    assert body["analyzer_provider"] == "fake"
    assert any("Kubernetes" in warning for warning in body["warnings"])


def test_llm_receives_only_professional_facts_and_delimited_job(client, auth_headers):
    jobs = setup_jobs(client, auth_headers)
    llm = FakeLLM(RequirementAnalysis(requirements=[], summary="", confidence=0.5))
    use_llm(llm)

    analyze(client, auth_headers, jobs["mock-001"])

    prompt = llm.prompts[0]
    assert '<untrusted_data source="vaga">' in prompt
    for personal_data in ("ana@example.com", "91234-5678", "Ana Souza", "github.com/ana"):
        assert personal_data not in prompt


def test_llm_failure_returns_502_and_is_recorded(client, auth_headers):
    jobs = setup_jobs(client, auth_headers)
    use_llm(FakeLLM(error=AIProviderError("O provedor de IA não respondeu a tempo.")))

    response = analyze(client, auth_headers, jobs["mock-001"])
    history = client.get(f"/api/v1/jobs/{jobs['mock-001']}/analyses", headers=auth_headers)

    assert response.status_code == 502
    assert history.json()[0]["status"] == "failed"


def test_preferences_defaults_and_validation(client, auth_headers):
    defaults = client.get("/api/v1/ai/preferences", headers=auth_headers).json()
    zero_weights = {name: 0 for name in defaults["weights"]}

    assert defaults["min_score"] == 65
    assert defaults["weights"]["skills"] == 40
    for payload in ({"min_score": 101}, {"weights": zero_weights}, {"writing_samples": ["a"] * 4}):
        response = client.put("/api/v1/ai/preferences", json=payload, headers=auth_headers)
        assert response.status_code == 422, payload


def test_analysis_of_other_users_job_is_not_found(client, db_session, auth_headers):
    jobs = setup_jobs(client, auth_headers)
    other = create_test_user(db_session, "other@example.com")
    other_headers = {"Authorization": f"Bearer {login(client, other.email)['access_token']}"}

    assert analyze(client, other_headers, jobs["mock-001"]).status_code == 404
