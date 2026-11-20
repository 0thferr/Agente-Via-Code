"""Critérios determinísticos: salário, modalidade, localização e senioridade.

Estes critérios são calculados por código, nunca pela IA. Os três primeiros
podem ser eliminatórios, mas só quando os dados da vaga são conhecidos: dado
ausente resulta em UNKNOWN, nunca em descarte.
"""

from dataclasses import dataclass

from app.core.text_normalization import normalize_text
from app.models.enums import FitStatus, Seniority, WorkModel
from app.models.job import JobOpportunity
from app.services.analysis.profile_facts import ProfileFacts

FULL_SCORE = 100
SENIORITY_ORDER = {
    Seniority.INTERN: 0,
    Seniority.JUNIOR: 1,
    Seniority.MID: 2,
    Seniority.SENIOR: 3,
    Seniority.SPECIALIST: 3,
    Seniority.LEAD: 4,
}
# Vaga um nível acima é um desafio possível; um nível abaixo, um passo atrás.
SENIORITY_ONE_LEVEL_ABOVE_SCORE = 50
SENIORITY_ONE_LEVEL_BELOW_SCORE = 70


@dataclass(frozen=True)
class CriterionResult:
    score: int | None
    fit: FitStatus
    reason: str
    eliminatory: bool = False


def evaluate_salary(job: JobOpportunity, facts: ProfileFacts) -> CriterionResult:
    expectation = facts.salary_expectation_min
    if expectation is None:
        return CriterionResult(None, FitStatus.UNKNOWN, "Pretensão salarial não cadastrada.")
    if job.salary_min is None and job.salary_max is None:
        return CriterionResult(None, FitStatus.UNKNOWN, "A vaga não informa salário.")
    if job.salary_currency != facts.salary_currency:
        return CriterionResult(
            None, FitStatus.UNKNOWN, "Salário da vaga em moeda diferente da pretensão."
        )

    currency = facts.salary_currency
    if job.salary_max is not None and job.salary_max < expectation:
        return CriterionResult(
            0,
            FitStatus.MISMATCH,
            f"Salário máximo da vaga ({job.salary_max} {currency}) abaixo da pretensão "
            f"mínima ({expectation} {currency}).",
            eliminatory=True,
        )
    if job.salary_max is not None or (job.salary_min is not None and job.salary_min >= expectation):
        return CriterionResult(FULL_SCORE, FitStatus.MATCH, "Faixa salarial atende à pretensão.")
    return CriterionResult(
        None,
        FitStatus.UNKNOWN,
        "A vaga informa só o salário mínimo, abaixo da pretensão; o máximo é desconhecido.",
    )


def evaluate_work_model(job: JobOpportunity, facts: ProfileFacts) -> CriterionResult:
    if job.work_model is None:
        return CriterionResult(None, FitStatus.UNKNOWN, "A vaga não informa a modalidade.")
    if not facts.desired_work_models or job.work_model in facts.desired_work_models:
        return CriterionResult(
            FULL_SCORE, FitStatus.MATCH, f"Modalidade {job.work_model} é aceita."
        )
    return CriterionResult(
        0,
        FitStatus.MISMATCH,
        f"Modalidade {job.work_model} não está entre as aceitas pelo candidato.",
        eliminatory=True,
    )


def evaluate_location(job: JobOpportunity, facts: ProfileFacts) -> CriterionResult:
    if job.work_model is WorkModel.REMOTE:
        return CriterionResult(FULL_SCORE, FitStatus.MATCH, "Vaga remota: localização indiferente.")
    if not facts.accepted_locations:
        return CriterionResult(None, FitStatus.UNKNOWN, "Nenhuma localização aceita cadastrada.")
    if not job.location:
        return CriterionResult(None, FitStatus.UNKNOWN, "A vaga não informa a localização.")

    job_location = normalize_text(job.location)
    if any(normalize_text(place) in job_location for place in facts.accepted_locations):
        return CriterionResult(FULL_SCORE, FitStatus.MATCH, f"Localização {job.location} aceita.")

    # Só é eliminatório quando sabemos que a vaga exige presença física.
    requires_presence = job.work_model in {WorkModel.ONSITE, WorkModel.HYBRID}
    return CriterionResult(
        0,
        FitStatus.MISMATCH,
        f"Localização {job.location} fora das localizações aceitas.",
        eliminatory=requires_presence,
    )


def evaluate_seniority(job: JobOpportunity, facts: ProfileFacts) -> CriterionResult:
    if facts.seniority is None:
        return CriterionResult(None, FitStatus.UNKNOWN, "Senioridade do candidato não cadastrada.")
    if job.seniority is None:
        return CriterionResult(None, FitStatus.UNKNOWN, "A vaga não informa a senioridade.")

    difference = SENIORITY_ORDER[job.seniority] - SENIORITY_ORDER[facts.seniority]
    if difference == 0:
        return CriterionResult(FULL_SCORE, FitStatus.MATCH, "Senioridade compatível.")
    if difference == 1:
        return CriterionResult(
            SENIORITY_ONE_LEVEL_ABOVE_SCORE,
            FitStatus.PARTIAL,
            f"Vaga um nível acima ({job.seniority}) da senioridade declarada.",
        )
    if difference == -1:
        return CriterionResult(
            SENIORITY_ONE_LEVEL_BELOW_SCORE,
            FitStatus.PARTIAL,
            f"Vaga um nível abaixo ({job.seniority}) da senioridade declarada.",
        )
    return CriterionResult(
        0, FitStatus.MISMATCH, f"Senioridade da vaga ({job.seniority}) distante da declarada."
    )
