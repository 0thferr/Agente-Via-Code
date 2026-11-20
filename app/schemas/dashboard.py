"""Contratos dos indicadores do dashboard."""

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Self

from pydantic import AwareDatetime, BaseModel, Field, StringConstraints, model_validator

from app.models.enums import ApplicationStatus

DEFAULT_RANGE = timedelta(days=30)
MAX_RANGE = timedelta(days=366)
FilterText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class Period(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class DashboardFilters(BaseModel):
    date_from: AwareDatetime | None = Field(default=None, description="Padrão: 30 dias atrás.")
    date_to: AwareDatetime | None = Field(default=None, description="Padrão: agora.")
    provider_key: Annotated[str, StringConstraints(max_length=50)] | None = None
    company: FilterText | None = None
    title: FilterText | None = Field(default=None, description="Filtra pelo cargo da vaga.")
    status: ApplicationStatus | None = Field(
        default=None, description="Filtra as métricas de candidaturas pelo status atual."
    )
    min_score: int | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def resolve_range(self) -> Self:
        self.date_to = self.date_to or datetime.now(UTC)
        self.date_from = self.date_from or self.date_to - DEFAULT_RANGE
        if self.date_from > self.date_to:
            raise ValueError("date_from deve ser anterior a date_to.")
        if self.date_to - self.date_from > MAX_RANGE:
            raise ValueError("O intervalo máximo é de 366 dias.")
        return self


class TimelineFilters(DashboardFilters):
    period: Period = Period.DAY


class OverviewOut(BaseModel):
    date_from: datetime
    date_to: datetime
    jobs_found: int
    jobs_analyzed: int
    jobs_qualified: int
    jobs_rejected: int
    average_score: float | None
    applications_awaiting_approval: int
    applications_submitted: int
    assessments: int
    interviews: int
    offers: int
    hires: int
    interview_rate: float | None = Field(description="Entrevistas / candidaturas enviadas.")
    offer_rate: float | None = Field(description="Propostas / candidaturas enviadas.")


class FunnelStage(BaseModel):
    stage: str
    count: int
    rate_from_previous: float | None


class FunnelOut(BaseModel):
    date_from: datetime
    date_to: datetime
    stages: list[FunnelStage]


class PlatformMetrics(BaseModel):
    provider_key: str
    jobs_found: int
    jobs_qualified: int
    average_score: float | None
    applications_submitted: int
    interviews: int


class TimelinePoint(BaseModel):
    period_start: datetime
    jobs_found: int = 0
    applications_submitted: int = 0
    interviews: int = 0
    agent_runs_succeeded: int = 0
    agent_runs_failed: int = 0


class TimelineOut(BaseModel):
    period: Period
    date_from: datetime
    date_to: datetime
    points: list[TimelinePoint]
