"""Construtores de objetos de domínio para testes unitários (sem banco)."""

from datetime import UTC, datetime
from decimal import Decimal

from app.models.enums import Seniority, WorkModel
from app.models.job import JobOpportunity
from app.services.analysis.profile_facts import ProfileFact, ProfileFacts


def make_job(**overrides) -> JobOpportunity:
    values = {
        "title": "Desenvolvedora Backend Python",
        "company": "Acme",
        "location": "São Paulo, SP",
        "work_model": WorkModel.REMOTE,
        "seniority": Seniority.JUNIOR,
        "description": "Vaga para APIs em Python.",
        "requirements": ["Python", "SQL"],
        "technologies": ["Python", "Docker"],
        "salary_min": Decimal("4000"),
        "salary_max": Decimal("6000"),
        "salary_currency": "BRL",
    }
    values.update(overrides)
    return JobOpportunity(**values)


def make_facts(**overrides) -> ProfileFacts:
    values = {
        "facts": [
            ProfileFact(ref="skill:1", label="Python", kind="skill"),
            ProfileFact(ref="skill:2", label="C", kind="skill"),
            ProfileFact(
                ref="exp:1",
                label="Desenvolvedora Júnior em Empresa X",
                kind="experience",
                details={"technologies": ["FastAPI", "PostgreSQL"]},
            ),
            ProfileFact(ref="lang:1", label="Inglês", kind="language"),
        ],
        "seniority": Seniority.JUNIOR,
        "desired_titles": ["Desenvolvedora Backend"],
        "desired_work_models": [WorkModel.REMOTE, WorkModel.HYBRID],
        "accepted_locations": ["São Paulo"],
        "salary_expectation_min": Decimal("5000"),
        "salary_currency": "BRL",
        "summary": "Desenvolvedora focada em APIs.",
        "profile_version": datetime(2026, 11, 1, tzinfo=UTC).isoformat(),
    }
    values.update(overrides)
    return ProfileFacts(**values)
