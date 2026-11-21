"""Estruturação do currículo: leitor simulado, leitor com IA e validação anti-invenção."""

from app.ai.llm import AIProviderError
from app.services.resumes.parsing import (
    HeuristicResumeParser,
    LLMResumeParser,
    ParsedEducation,
    ParsedExperience,
    ParsedLanguage,
    ParsedResume,
    build_structured_draft,
    redact_contacts,
)

RESUME_TEXT = """Ana Souza
Desenvolvedora Backend Júnior
ana.souza@example.com | +55 11 91234-5678 | github.com/anasouza

Resumo
Desenvolvedora focada em APIs com Python e bancos relacionais.

Competências
Python, FastAPI, PostgreSQL, Docker

Idiomas
Inglês - Avançado
Espanhol: Básico

Experiência
Desenvolvedora Júnior - Empresa X (02/2024 - atual)
"""


class FakeLLM:
    provider_name = "fake"
    model_name = "fake-model"

    def __init__(self, response=None, error=None) -> None:
        self.response, self.error = response, error
        self.prompts: list[str] = []

    def generate(self, *, system_prompt, user_prompt, output_model):
        self.prompts.append(user_prompt)
        if self.error:
            raise self.error
        return self.response


def empty_resume(**overrides) -> ParsedResume:
    values = {
        "full_name": None,
        "current_title": None,
        "summary": None,
        "skills": [],
        "languages": [],
        "experiences": [],
        "educations": [],
        "certifications": [],
    }
    values.update(overrides)
    return ParsedResume(**values)


# --- Leitor simulado -------------------------------------------------------------------


def test_heuristic_parser_reads_sections():
    parsed = HeuristicResumeParser().parse(RESUME_TEXT)

    assert parsed.full_name == "Ana Souza"
    assert parsed.current_title == "Desenvolvedora Backend Júnior"
    assert parsed.summary.startswith("Desenvolvedora focada em APIs")
    assert parsed.skills == ["Python", "FastAPI", "PostgreSQL", "Docker"]
    assert [(lang.name, lang.level) for lang in parsed.languages] == [
        ("Inglês", "Avançado"),
        ("Espanhol", "Básico"),
    ]
    # Experiências e formação ficam para revisão: o leitor simulado não as interpreta.
    assert parsed.experiences == []


# --- Rascunho e validação --------------------------------------------------------------


def test_draft_maps_to_profile_fields():
    parsed = HeuristicResumeParser().parse(RESUME_TEXT)

    draft = build_structured_draft(parsed, RESUME_TEXT)

    profile = draft.profile
    assert profile["full_name"] == "Ana Souza"
    assert {"name": "Python", "category": "technical"} in profile["skills"]
    assert {"name": "Inglês", "proficiency": "advanced"} in profile["languages"]
    assert {"name": "Espanhol", "proficiency": "basic"} in profile["languages"]


def test_items_not_in_resume_text_are_discarded():
    invented = empty_resume(
        skills=["Python", "Kubernetes"],
        experiences=[
            ParsedExperience(
                company="Empresa X",
                title="Desenvolvedora Júnior",
                start="2024-02",
                end=None,
                is_current=True,
                description=None,
            ),
            ParsedExperience(
                company="Google",
                title="Staff Engineer",
                start="2015-01",
                end="2020-01",
                is_current=False,
                description=None,
            ),
        ],
        educations=[
            ParsedEducation(institution="MIT", degree="PhD", field=None, status="completed")
        ],
        certifications=["AWS Solutions Architect"],
    )

    draft = build_structured_draft(invented, RESUME_TEXT)

    assert [skill["name"] for skill in draft.profile["skills"]] == ["Python"]
    assert [exp["company"] for exp in draft.profile["experiences"]] == ["Empresa X"]
    assert draft.profile["experiences"][0]["start_date"] == "2024-02-01"
    assert draft.profile["educations"] == []
    assert draft.profile["certifications"] == []
    warnings = " ".join(draft.warnings)
    for invented_item in ("Kubernetes", "Google", "MIT", "AWS Solutions Architect"):
        assert invented_item in warnings


def test_unknown_language_level_is_left_for_review():
    parsed = empty_resume(languages=[ParsedLanguage(name="Inglês", level="mais ou menos")])

    draft = build_structured_draft(parsed, "Inglês mais ou menos")

    assert draft.profile["languages"] == [{"name": "Inglês", "proficiency": None}]
    assert any("nível" in warning for warning in draft.warnings)


# --- Leitor com IA ---------------------------------------------------------------------


def test_contacts_are_removed_before_sending_to_ai():
    redacted = redact_contacts(RESUME_TEXT)

    for contact in ("ana.souza@example.com", "91234-5678", "github.com/anasouza"):
        assert contact not in redacted
    assert "Python, FastAPI" in redacted


def test_llm_parser_sends_redacted_and_delimited_text():
    llm = FakeLLM(empty_resume(skills=["Python"]))

    parsed = LLMResumeParser(llm).parse(RESUME_TEXT)

    prompt = llm.prompts[0]
    assert parsed.skills == ["Python"]
    assert '<untrusted_data source="curriculo">' in prompt
    assert "ana.souza@example.com" not in prompt


def test_llm_failure_propagates_as_provider_error():
    llm = FakeLLM(error=AIProviderError("fora do ar"))

    try:
        LLMResumeParser(llm).parse(RESUME_TEXT)
    except AIProviderError as exc:
        assert "fora do ar" in str(exc)
    else:
        raise AssertionError("esperava AIProviderError")
