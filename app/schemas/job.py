"""Contratos de entrada e saída de vagas e fontes de vagas."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.integrations.job_providers.base import IntegrationStatus, ProviderCapability
from app.models.enums import DiscoveryMethod, Seniority, WorkModel
from app.schemas.profile import ShortText, TagList, UrlText

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20

CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
SearchText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class ProviderOut(BaseModel):
    key: str
    name: str
    integration_status: IntegrationStatus
    capabilities: list[ProviderCapability]
    notes: str


class JobSourceCreate(BaseModel):
    provider_key: Annotated[str, StringConstraints(min_length=1, max_length=50)]
    is_enabled: bool = True


class JobSourceUpdate(BaseModel):
    is_enabled: bool


class JobSourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider_key: str
    is_enabled: bool
    last_synced_at: datetime | None
    created_at: datetime


class JobSearchFilters(BaseModel):
    title: SearchText | None = None
    keywords: list[SearchText] = Field(default_factory=list, max_length=20)
    technologies: list[SearchText] = Field(default_factory=list, max_length=20)
    seniority: Seniority | None = None
    location: SearchText | None = None
    work_model: WorkModel | None = None
    min_salary: Decimal | None = Field(default=None, ge=0)
    company: SearchText | None = None
    posted_after: AwareDatetime | None = None


class JobSearchRequest(BaseModel):
    filters: JobSearchFilters = Field(default_factory=JobSearchFilters)
    source_ids: list[uuid.UUID] | None = Field(
        default=None, description="Fontes a consultar. Vazio: todas as fontes habilitadas."
    )
    incremental: bool = Field(
        default=False,
        description="Busca só vagas publicadas desde a última sincronização de cada fonte.",
    )


class SourceSearchResult(BaseModel):
    source_id: uuid.UUID
    provider_key: str
    fetched: int = 0
    created: int = 0
    duplicates: int = 0
    skipped: int = Field(default=0, description="Vagas ignoradas por dados incompletos.")
    error: str | None = None


class JobSearchResponse(BaseModel):
    results: list[SourceSearchResult]
    created_job_ids: list[uuid.UUID]


class JobImportRequest(BaseModel):
    """Vaga cadastrada manualmente a partir de um link (fluxo assistido)."""

    url: UrlText
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]
    company: ShortText
    description: Annotated[str, StringConstraints(max_length=20_000)] = ""
    location: ShortText | None = None
    work_model: WorkModel | None = None
    seniority: Seniority | None = None
    requirements: TagList = Field(default_factory=list)
    technologies: TagList = Field(default_factory=list)
    salary_min: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    salary_max: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    salary_currency: CurrencyCode | None = None
    posted_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_salary(self) -> Self:
        if self.salary_min and self.salary_max and self.salary_max < self.salary_min:
            raise ValueError("salary_max não pode ser menor que salary_min.")
        has_salary = self.salary_min is not None or self.salary_max is not None
        if has_salary and self.salary_currency is None:
            raise ValueError("Informe a moeda (salary_currency) junto com o salário.")
        return self


class JobSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider_key: str
    discovery_method: DiscoveryMethod
    url: str
    title: str
    company: str
    location: str | None
    work_model: WorkModel | None
    seniority: Seniority | None
    salary_min: Decimal | None
    salary_max: Decimal | None
    salary_currency: str | None
    posted_at: datetime | None
    created_at: datetime


class JobDetailOut(JobSummaryOut):
    external_id: str | None
    description: str
    requirements: list[str]
    technologies: list[str]
    last_seen_at: datetime


class JobImportResponse(BaseModel):
    job: JobDetailOut
    created: bool = Field(description="False quando a vaga já existia (importação idempotente).")


class JobListFilters(BaseModel):
    q: SearchText | None = Field(default=None, description="Busca no cargo e na empresa.")
    company: SearchText | None = None
    provider_key: str | None = None
    work_model: WorkModel | None = None
    seniority: Seniority | None = None
    posted_after: AwareDatetime | None = None
    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    offset: int = Field(default=0, ge=0)


class JobListResponse(BaseModel):
    items: list[JobSummaryOut]
    total: int
    limit: int
    offset: int
