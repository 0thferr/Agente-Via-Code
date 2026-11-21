"""Upload de currículo com estruturação automática (sempre como rascunho)."""

from app.ai.llm import AIProviderError
from app.main import app
from app.services.resumes.parsing import LLMResumeParser
from app.wiring import get_resume_parser
from tests.pdf_factory import make_pdf
from tests.unit.test_resume_parsing import FakeLLM

LINES = [
    "Ana Souza",
    "Desenvolvedora Backend",
    "ana.souza@example.com",
    "Competências",
    "Python, FastAPI",
    "Idiomas",
    "Inglês - Avançado",
]


def upload(client, headers, lines=LINES):
    files = {"file": ("cv.pdf", make_pdf(lines), "application/pdf")}
    return client.post("/api/v1/profile/resumes", files=files, headers=headers)


def test_upload_returns_structured_draft_for_review(client, auth_headers):
    response = upload(client, auth_headers)

    data = response.json()["extracted_data"]
    assert response.status_code == 201, response.text
    assert response.json()["import_status"] == "pending_review"
    assert data["profile_draft"]["contact_email"] == "ana.souza@example.com"
    assert data["profile_draft"]["full_name"] == "Ana Souza"
    assert [s["name"] for s in data["profile_draft"]["skills"]] == ["Python", "FastAPI"]
    assert data["parser"] == {"provider": "simulated", "model": "section-heuristics-v1"}
    # O perfil continua intocado até a confirmação.
    assert client.get("/api/v1/profile", headers=auth_headers).status_code == 404


def test_ai_failure_keeps_basic_draft(client, auth_headers):
    llm = FakeLLM(error=AIProviderError("O provedor de IA não respondeu a tempo."))
    app.dependency_overrides[get_resume_parser] = lambda: LLMResumeParser(llm)

    response = upload(client, auth_headers)

    data = response.json()["extracted_data"]
    assert response.status_code == 201
    assert data["profile_draft"]["contact_email"] == "ana.souza@example.com"
    assert "skills" not in data["profile_draft"]
    assert any("Estruturação automática indisponível" in note for note in data["notes"])
