"""Cálculo da nota final, da confiança e da recomendação — sempre em código.

A nota é a média ponderada dos critérios com resultado conhecido. Critérios
desconhecidos ficam fora da média (não punem nem favorecem), mas reduzem a
confiança da análise.
"""

from dataclasses import dataclass

from app.models.enums import Recommendation, RequirementStatus
from app.services.analysis.requirements import RequirementAssessment

MANDATORY_REQUIREMENT_WEIGHT = 2
DESIRABLE_REQUIREMENT_WEIGHT = 1
STATUS_VALUES = {
    RequirementStatus.MET: 1.0,
    RequirementStatus.PARTIALLY_MET: 0.5,
    RequirementStatus.NOT_MET: 0.0,
}
INJECTION_CONFIDENCE_FACTOR = 0.7

SKILLS = "skills"
SENIORITY = "seniority"
SALARY = "salary"
LOCATION = "location"
WORK_MODEL = "work_model"
CRITERIA = (SKILLS, SENIORITY, SALARY, LOCATION, WORK_MODEL)

DEFAULT_WEIGHTS = {SKILLS: 40, SENIORITY: 20, SALARY: 15, LOCATION: 15, WORK_MODEL: 10}
DEFAULT_MIN_SCORE = 65


@dataclass(frozen=True)
class ScoreResult:
    score: int | None
    confidence: float


def requirements_score(assessments: list[RequirementAssessment]) -> int | None:
    """0 a 100: requisitos obrigatórios pesam o dobro; "unclear" fica de fora."""
    total_weight = 0.0
    achieved = 0.0
    for item in assessments:
        if item.status not in STATUS_VALUES:
            continue
        weight = MANDATORY_REQUIREMENT_WEIGHT if item.mandatory else DESIRABLE_REQUIREMENT_WEIGHT
        total_weight += weight
        achieved += weight * STATUS_VALUES[item.status]
    if total_weight == 0:
        return None
    return round(100 * achieved / total_weight)


def combine_scores(
    criterion_scores: dict[str, int | None],
    weights: dict[str, int],
    *,
    analyzer_confidence: float,
    injection_detected: bool,
) -> ScoreResult:
    known = {name: score for name, score in criterion_scores.items() if score is not None}
    known_weight = sum(weights[name] for name in known)
    total_weight = sum(weights[name] for name in criterion_scores)
    if known_weight == 0:
        return ScoreResult(score=None, confidence=0.0)

    score = round(sum(weights[name] * value for name, value in known.items()) / known_weight)
    coverage = known_weight / total_weight
    confidence = coverage * analyzer_confidence
    if injection_detected:
        confidence *= INJECTION_CONFIDENCE_FACTOR
    return ScoreResult(score=score, confidence=round(confidence, 2))


def decide(score: int | None, min_score: int, has_eliminatory_failure: bool) -> Recommendation:
    if has_eliminatory_failure:
        return Recommendation.REJECTED
    if score is None:
        return Recommendation.PENDING
    return Recommendation.QUALIFIED if score >= min_score else Recommendation.REJECTED
