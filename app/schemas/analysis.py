"""Contratos de preferências de IA, análises de compatibilidade e textos de apresentação."""

import uuid
from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.models.analysis import JobAnalysis
from app.models.enums import (
    AnalysisStatus,
    CoverLetterSource,
    CoverLetterTone,
    Recommendation,
    RequirementStatus,
)
from app.services.analysis.scoring import DEFAULT_MIN_SCORE, DEFAULT_WEIGHTS

MAX_WRITING_SAMPLES = 3
MAX_WRITING_SAMPLE_LENGTH = 3000
MIN_COVER_LETTER_LENGTH = 300
MAX_COVER_LETTER_LENGTH = 5000
DEFAULT_COVER_LETTER_LENGTH = 1500

MET_STATUSES = {RequirementStatus.MET, RequirementStatus.PARTIALLY_MET}
NOT_MET = RequirementStatus.NOT_MET

Weight = Annotated[int, Field(ge=0, le=100)]
WritingSample = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_WRITING_SAMPLE_LENGTH),
]


class ScoreWeights(BaseModel):
    skills: Weight = DEFAULT_WEIGHTS["skills"]
    seniority: Weight = DEFAULT_WEIGHTS["seniority"]
    salary: Weight = DEFAULT_WEIGHTS["salary"]
    location: Weight = DEFAULT_WEIGHTS["location"]
    work_model: Weight = DEFAULT_WEIGHTS["work_model"]

    @model_validator(mode="after")
    def require_positive_total(self) -> Self:
        if sum(self.model_dump().values()) == 0:
            raise ValueError("Ao menos um peso precisa ser maior que zero.")
        return self


class AIPreferencesIn(BaseModel):
    min_score: int = Field(default=DEFAULT_MIN_SCORE, ge=0, le=100)
    weights: ScoreWeights = Field(default_factory=ScoreWeights)
    cover_letter_tone: CoverLetterTone = CoverLetterTone.NEUTRAL
    writing_samples: list[WritingSample] = Field(
        default_factory=list,
        max_length=MAX_WRITING_SAMPLES,
        description="Textos escritos por você, usados só como referência de estilo.",
    )


class AIPreferencesOut(AIPreferencesIn):
    model_config = ConfigDict(from_attributes=True)


class RequirementOut(BaseModel):
    requirement: str
    mandatory: bool
    status: RequirementStatus
    evidence: list[str] = Field(description="Itens do perfil que comprovam o requisito.")


class CriterionOut(BaseModel):
    score: int | None
    fit: str
    reason: str
    eliminatory: bool


class JobAnalysisOut(BaseModel):
    """Resultado estruturado da análise de compatibilidade."""

    id: uuid.UUID
    job_id: uuid.UUID
    status: AnalysisStatus
    recommendation: Recommendation
    score: int | None = Field(description="Nota de 0 a 100; nula quando a análise está pendente.")
    confidence: float
    min_score: int
    weights: dict[str, int]
    criteria: dict[str, CriterionOut]
    matched_skills: list[str]
    missing_skills: list[str]
    unmet_mandatory_requirements: list[str]
    met_desirable_requirements: list[str]
    requirements: list[RequirementOut]
    decision_reasons: list[str]
    warnings: list[str]
    ai_summary: str | None
    analyzer_provider: str
    analyzer_model: str
    profile_version: str
    error: str | None
    created_at: datetime

    @classmethod
    def from_record(cls, record: JobAnalysis) -> Self:
        """Monta as listas-resumo (competências atendidas, ausentes...) a partir dos requisitos."""
        requirements = [RequirementOut(**item) for item in record.requirements]
        met = [item for item in requirements if item.status in MET_STATUSES]
        return cls(
            id=record.id,
            job_id=record.job_id,
            status=record.status,
            recommendation=record.recommendation,
            score=record.score,
            confidence=record.confidence,
            min_score=record.min_score,
            weights=record.weights,
            criteria=record.criteria,
            matched_skills=list(dict.fromkeys(label for item in met for label in item.evidence)),
            missing_skills=[item.requirement for item in requirements if item.status == NOT_MET],
            unmet_mandatory_requirements=[
                item.requirement
                for item in requirements
                if item.status == NOT_MET and item.mandatory
            ],
            met_desirable_requirements=[item.requirement for item in met if not item.mandatory],
            requirements=requirements,
            decision_reasons=record.decision_reasons,
            warnings=record.warnings,
            ai_summary=record.ai_summary,
            analyzer_provider=record.analyzer_provider,
            analyzer_model=record.analyzer_model,
            profile_version=record.profile_version,
            error=record.error,
            created_at=record.created_at,
        )


class CoverLetterGenerateRequest(BaseModel):
    tone: CoverLetterTone | None = Field(
        default=None, description="Se omitido, usa o tom das preferências."
    )
    max_characters: int = Field(
        default=DEFAULT_COVER_LETTER_LENGTH,
        ge=MIN_COVER_LETTER_LENGTH,
        le=MAX_COVER_LETTER_LENGTH,
        description="Limite de caracteres da plataforma de destino.",
    )


class CoverLetterRevisionRequest(BaseModel):
    content: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_COVER_LETTER_LENGTH),
    ]


class CoverLetterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: uuid.UUID
    analysis_id: uuid.UUID | None
    version: int
    source: CoverLetterSource
    content: str
    tone: CoverLetterTone
    max_characters: int
    validation_warnings: list[str]
    generator_provider: str | None
    generator_model: str | None
    created_at: datetime
