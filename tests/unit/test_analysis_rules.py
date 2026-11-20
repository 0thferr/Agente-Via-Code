from decimal import Decimal

import pytest

from app.models.enums import FitStatus, Recommendation, RequirementStatus, Seniority, WorkModel
from app.services.analysis import criteria
from app.services.analysis.requirements import RequirementAssessment
from app.services.analysis.scoring import (
    DEFAULT_WEIGHTS,
    combine_scores,
    decide,
    requirements_score,
)
from tests.factories import make_facts, make_job

# --- Salário ---------------------------------------------------------------------------


def test_salary_below_expectation_is_eliminatory():
    result = criteria.evaluate_salary(make_job(salary_max=Decimal("4500")), make_facts())

    assert result.eliminatory and result.fit is FitStatus.MISMATCH


def test_salary_within_expectation_matches():
    assert criteria.evaluate_salary(make_job(), make_facts()).score == 100


@pytest.mark.parametrize(
    "job_overrides",
    [
        {"salary_min": None, "salary_max": None},  # vaga não informa
        {"salary_currency": "USD"},  # moeda diferente
        {"salary_min": Decimal("3000"), "salary_max": None},  # só o mínimo, abaixo
    ],
)
def test_unknown_salary_is_never_eliminatory(job_overrides):
    result = criteria.evaluate_salary(make_job(**job_overrides), make_facts())

    assert not result.eliminatory
    assert result.score is None and result.fit is FitStatus.UNKNOWN


def test_salary_without_expectation_is_unknown():
    result = criteria.evaluate_salary(make_job(), make_facts(salary_expectation_min=None))

    assert result.fit is FitStatus.UNKNOWN


# --- Modalidade e localização ----------------------------------------------------------


def test_work_model_not_accepted_is_eliminatory():
    result = criteria.evaluate_work_model(make_job(work_model=WorkModel.ONSITE), make_facts())

    assert result.eliminatory


def test_work_model_without_preference_accepts_any():
    facts = make_facts(desired_work_models=[])

    assert criteria.evaluate_work_model(make_job(work_model=WorkModel.ONSITE), facts).score == 100


def test_unknown_work_model_is_not_eliminatory():
    result = criteria.evaluate_work_model(make_job(work_model=None), make_facts())

    assert not result.eliminatory and result.score is None


def test_remote_job_ignores_location():
    job = make_job(work_model=WorkModel.REMOTE, location="Manaus, AM")

    assert criteria.evaluate_location(job, make_facts()).score == 100


def test_onsite_job_outside_accepted_locations_is_eliminatory():
    job = make_job(work_model=WorkModel.ONSITE, location="Recife, PE")

    assert criteria.evaluate_location(job, make_facts()).eliminatory


def test_location_mismatch_with_unknown_work_model_is_not_eliminatory():
    job = make_job(work_model=None, location="Recife, PE")

    result = criteria.evaluate_location(job, make_facts())

    assert result.score == 0 and not result.eliminatory


def test_location_match_ignores_accents_and_case():
    job = make_job(work_model=WorkModel.HYBRID, location="SAO PAULO - SP")

    assert criteria.evaluate_location(job, make_facts()).fit is FitStatus.MATCH


# --- Senioridade ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("job_level", "expected"),
    [
        (Seniority.JUNIOR, 100),
        (Seniority.MID, 50),
        (Seniority.INTERN, 70),
        (Seniority.SENIOR, 0),
    ],
)
def test_seniority_scores(job_level, expected):
    result = criteria.evaluate_seniority(make_job(seniority=job_level), make_facts())

    assert result.score == expected
    assert not result.eliminatory


# --- Nota e decisão --------------------------------------------------------------------


def assessment(status, mandatory=True):
    return RequirementAssessment(
        requirement="x", mandatory=mandatory, status=status, evidence_refs=[]
    )


def test_requirements_score_weights_mandatory_double():
    items = [
        assessment(RequirementStatus.MET, mandatory=True),
        assessment(RequirementStatus.NOT_MET, mandatory=False),
    ]

    assert requirements_score(items) == round(100 * 2 / 3)


def test_requirements_score_ignores_unclear():
    items = [assessment(RequirementStatus.MET), assessment(RequirementStatus.UNCLEAR)]

    assert requirements_score(items) == 100


def test_requirements_score_without_assessable_items_is_none():
    assert requirements_score([assessment(RequirementStatus.UNCLEAR)]) is None


def test_combine_scores_excludes_unknown_criteria_but_lowers_confidence():
    scores = {"skills": 80, "seniority": None, "salary": 100, "location": None, "work_model": 100}

    result = combine_scores(
        scores, DEFAULT_WEIGHTS, analyzer_confidence=1.0, injection_detected=False
    )

    assert result.score == round((40 * 80 + 15 * 100 + 10 * 100) / 65)
    assert result.confidence == round(65 / 100, 2)


def test_injection_reduces_confidence():
    scores = {"skills": 80, "seniority": 100, "salary": 100, "location": 100, "work_model": 100}
    clean = combine_scores(
        scores, DEFAULT_WEIGHTS, analyzer_confidence=1.0, injection_detected=False
    )
    suspicious = combine_scores(
        scores, DEFAULT_WEIGHTS, analyzer_confidence=1.0, injection_detected=True
    )

    assert suspicious.score == clean.score
    assert suspicious.confidence < clean.confidence


@pytest.mark.parametrize(
    ("score", "eliminatory", "expected"),
    [
        (90, True, Recommendation.REJECTED),
        (None, False, Recommendation.PENDING),
        (65, False, Recommendation.QUALIFIED),
        (64, False, Recommendation.REJECTED),
    ],
)
def test_decide(score, eliminatory, expected):
    assert decide(score, 65, eliminatory) is expected
