"""Fatos verificáveis do perfil, com referências curtas usadas como evidência.

A IA recebe só estes fatos (nada de nome, e-mail, telefone ou links: o mínimo
necessário, conforme a LGPD) e precisa citar as referências ("skill:1",
"exp:2"...). Assim conseguimos verificar em código se cada afirmação tem lastro.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.models.enums import Seniority, WorkModel
from app.models.profile import ProfessionalProfile


@dataclass(frozen=True)
class ProfileFact:
    ref: str
    label: str
    kind: str
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ProfileFacts:
    facts: list[ProfileFact]
    seniority: Seniority | None
    desired_titles: list[str]
    desired_work_models: list[WorkModel]
    accepted_locations: list[str]
    salary_expectation_min: Decimal | None
    salary_currency: str | None
    summary: str | None
    profile_version: str

    def by_ref(self) -> dict[str, ProfileFact]:
        return {fact.ref: fact for fact in self.facts}

    def known_terms(self) -> list[str]:
        """Nomes de competências, tecnologias, idiomas e certificações declarados."""
        terms: list[str] = []
        for fact in self.facts:
            if fact.kind in {"skill", "language", "certification"}:
                terms.append(fact.label)
            if fact.kind == "experience":
                terms.extend(str(tech) for tech in fact.details.get("technologies", []))
        return list(dict.fromkeys(terms))

    def to_prompt_data(self) -> dict[str, object]:
        return {
            "senioridade_declarada": self.seniority,
            "cargos_desejados": self.desired_titles,
            "resumo": self.summary,
            "fatos": [
                {"ref": fact.ref, "tipo": fact.kind, "descricao": fact.label, **fact.details}
                for fact in self.facts
            ],
        }


def build_profile_facts(profile: ProfessionalProfile) -> ProfileFacts:
    facts: list[ProfileFact] = []
    for index, skill in enumerate(profile.skills, start=1):
        facts.append(
            ProfileFact(
                ref=f"skill:{index}",
                label=skill.name,
                kind="skill",
                details={
                    "categoria": skill.category,
                    "nivel": skill.level,
                    "anos": skill.years_of_experience,
                },
            )
        )
    for index, experience in enumerate(profile.experiences, start=1):
        facts.append(
            ProfileFact(
                ref=f"exp:{index}",
                label=f"{experience.title} em {experience.company}",
                kind="experience",
                details={
                    "inicio": _format_date(experience.start_date),
                    "fim": "atual" if experience.is_current else _format_date(experience.end_date),
                    "technologies": list(experience.technologies),
                    "descricao": experience.description,
                },
            )
        )
    for index, education in enumerate(profile.educations, start=1):
        facts.append(
            ProfileFact(
                ref=f"edu:{index}",
                label=f"{education.degree} - {education.institution}",
                kind="education",
                details={"area": education.field_of_study, "situacao": education.status},
            )
        )
    for index, language in enumerate(profile.languages, start=1):
        facts.append(
            ProfileFact(
                ref=f"lang:{index}",
                label=language.name,
                kind="language",
                details={"nivel": language.proficiency},
            )
        )
    for index, certification in enumerate(profile.certifications, start=1):
        facts.append(
            ProfileFact(
                ref=f"cert:{index}",
                label=certification.name,
                kind="certification",
                details={"emissor": certification.issuer},
            )
        )

    return ProfileFacts(
        facts=facts,
        seniority=profile.seniority,
        desired_titles=list(profile.desired_titles),
        desired_work_models=[WorkModel(model) for model in profile.desired_work_models],
        accepted_locations=list(profile.accepted_locations),
        salary_expectation_min=profile.salary_expectation_min,
        salary_currency=profile.salary_currency,
        summary=profile.summary,
        profile_version=profile.updated_at.isoformat(),
    )


def _format_date(value: date | None) -> str | None:
    return value.strftime("%Y-%m") if value else None
