from app.api import deps
from app.main import app
from app.services.cover_letters.writers import Claim, CoverLetterDraft, LLMCoverLetterWriter
from tests.conftest import create_test_user, login
from tests.integration.test_analysis import FakeLLM, analyze, setup_jobs


def generate(client, headers, job_id, **payload):
    return client.post(f"/api/v1/jobs/{job_id}/cover-letters", json=payload, headers=headers)


def test_generated_letter_uses_only_profile_facts(client, auth_headers):
    jobs = setup_jobs(client, auth_headers)
    analyze(client, auth_headers, jobs["mock-001"])

    response = generate(client, auth_headers, jobs["mock-001"])

    letter = response.json()
    assert response.status_code == 201, response.text
    assert letter["version"] == 1
    assert letter["source"] == "generated"
    assert letter["generator_provider"] == "simulated"
    assert "Acme Tech" in letter["content"]
    assert "Desenvolvedora Júnior em Empresa X" in letter["content"]
    assert letter["validation_warnings"] == []
    assert letter["analysis_id"] is not None


def test_letter_respects_character_limit_and_tone(client, auth_headers):
    jobs = setup_jobs(client, auth_headers)

    letter = generate(
        client, auth_headers, jobs["mock-001"], max_characters=300, tone="formal"
    ).json()

    assert len(letter["content"]) <= 300
    assert letter["content"].startswith("Prezada equipe")


def test_each_generation_creates_a_new_version(client, auth_headers):
    jobs = setup_jobs(client, auth_headers)
    generate(client, auth_headers, jobs["mock-001"])
    generate(client, auth_headers, jobs["mock-001"])

    letters = client.get(f"/api/v1/jobs/{jobs['mock-001']}/cover-letters", headers=auth_headers)

    assert [letter["version"] for letter in letters.json()] == [2, 1]


def test_user_revision_creates_version_and_flags_unsupported_claims(client, auth_headers):
    jobs = setup_jobs(client, auth_headers)
    original = generate(client, auth_headers, jobs["mock-001"]).json()

    response = client.post(
        f"/api/v1/cover-letters/{original['id']}/revisions",
        json={"content": "Tenho experiência sólida com Docker e Python."},
        headers=auth_headers,
    )

    revision = response.json()
    assert response.status_code == 201
    assert revision["version"] == 2 and revision["source"] == "user_edit"
    assert any("Docker" in warning for warning in revision["validation_warnings"])


def test_revision_over_limit_is_rejected(client, auth_headers):
    jobs = setup_jobs(client, auth_headers)
    original = generate(client, auth_headers, jobs["mock-001"], max_characters=300).json()

    response = client.post(
        f"/api/v1/cover-letters/{original['id']}/revisions",
        json={"content": "a" * 301},
        headers=auth_headers,
    )

    assert response.status_code == 422


def test_llm_letter_with_invented_claims_is_flagged_and_trimmed(client, auth_headers):
    jobs = setup_jobs(client, auth_headers)
    long_text = "Sou especialista em Docker. " + "Frase extra de preenchimento. " * 20
    llm = FakeLLM(
        CoverLetterDraft(
            content=long_text,
            claims=[Claim(statement="Sou especialista em Docker.", evidence_refs=["skill:42"])],
        )
    )
    app.dependency_overrides[deps.get_cover_letter_writer] = lambda: LLMCoverLetterWriter(llm)

    letter = generate(client, auth_headers, jobs["mock-001"], max_characters=300).json()

    warnings = " ".join(letter["validation_warnings"])
    assert len(letter["content"]) <= 300
    assert "sem evidência" in warnings
    assert "Docker" in warnings
    assert "foi encurtado" in warnings
    assert letter["generator_model"] == "fake-model"


def test_cover_letters_are_private(client, db_session, auth_headers):
    jobs = setup_jobs(client, auth_headers)
    letter = generate(client, auth_headers, jobs["mock-001"]).json()
    other = create_test_user(db_session, "other@example.com")
    other_headers = {"Authorization": f"Bearer {login(client, other.email)['access_token']}"}

    response = client.get(f"/api/v1/cover-letters/{letter['id']}", headers=other_headers)
    assert response.status_code == 404
