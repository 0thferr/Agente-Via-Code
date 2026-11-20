"""Contratos de entrada e saída de candidaturas."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from app.integrations.job_providers.registry import MANUAL_PLATFORM_KEYS
from app.models.application import (
    DEFAULT_ASSISTED_DAILY_LIMITS,
    DEFAULT_MAX_DAILY_SUBMISSIONS,
    Application,
)
from app.models.enums import (
    ApplicationMode,
    ApplicationStatus,
    ConfirmationSource,
    EventActor,
)
from app.schemas.job import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

MAX_ANSWERS = 30
Comment = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]
AnswerKey = Annotated[str, StringConstraints(min_length=1, max_length=50)]
AnswerValue = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)
]
ProgressStatus = Literal["assessment", "interview", "offer", "hired", "closed"]


class ApplicationSettingsIn(BaseModel):
    mode: ApplicationMode = ApplicationMode.SUPERVISED
    auto_submit_providers: list[Annotated[str, StringConstraints(max_length=50)]] = Field(
        default_factory=list,
        max_length=20,
        description="Provedores em que o modo automático pode enviar sem aprovação.",
    )
    max_daily_submissions: int = Field(default=DEFAULT_MAX_DAILY_SUBMISSIONS, ge=0, le=100)
    assisted_daily_limits: dict[str, Annotated[int, Field(ge=0, le=100)]] = Field(
        default_factory=lambda: dict(DEFAULT_ASSISTED_DAILY_LIMITS),
        description="Modo assistido: limite diário por plataforma sem envio integrado "
        "(candidaturas enviadas hoje + kits aguardando envio).",
    )

    @field_validator("assisted_daily_limits")
    @classmethod
    def validate_assisted_platforms(cls, value: dict[str, int]) -> dict[str, int]:
        unknown = set(value) - MANUAL_PLATFORM_KEYS
        if unknown:
            raise ValueError(
                f"Plataformas inválidas: {sorted(unknown)}. Use: {sorted(MANUAL_PLATFORM_KEYS)}."
            )
        return value


class ApplicationSettingsOut(ApplicationSettingsIn):
    pass


class ApplicationCreate(BaseModel):
    job_id: uuid.UUID


class ApplicationPrepareRequest(BaseModel):
    resume_id: uuid.UUID | None = Field(
        default=None, description="Currículo a anexar. Se omitido, usa o mais recente."
    )


class ApplicationApproveRequest(BaseModel):
    cover_letter_id: uuid.UUID | None = Field(
        default=None, description="Versão do texto revisada. Se omitida, usa a atual."
    )
    resume_id: uuid.UUID | None = None
    acknowledge_warnings: bool = Field(
        default=False, description="Obrigatório quando o texto tem alertas de validação."
    )
    comment: Comment | None = None


class ApplicationRejectRequest(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class ApplicationAnswersRequest(BaseModel):
    answers: dict[AnswerKey, AnswerValue] = Field(max_length=MAX_ANSWERS)


class MarkAppliedRequest(BaseModel):
    """Registro de candidatura feita pelo próprio usuário fora do sistema."""

    confirmation: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=3, max_length=200)
    ] = Field(description="Protocolo, código ou assunto do e-mail de confirmação da plataforma.")
    applied_at: AwareDatetime | None = None


class ProgressUpdateRequest(BaseModel):
    status: ProgressStatus
    note: Comment | None = None


class PendingQuestionOut(BaseModel):
    id: str
    label: str


class ApplicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: uuid.UUID
    status: ApplicationStatus
    analysis_id: uuid.UUID | None
    cover_letter_id: uuid.UUID | None
    resume_id: uuid.UUID | None
    compatibility_score: int | None
    compatibility_summary: str | None
    met_requirements: list[str]
    resume_highlights: list[str] = Field(
        description="Itens do perfil mais relevantes para a vaga, na ordem sugerida."
    )
    pending_questions: list[PendingQuestionOut]
    answered_questions: list[str] = Field(description="IDs das perguntas já respondidas.")
    submission_channel: str | None
    confirmation_source: ConfirmationSource | None
    external_confirmation_id: str | None
    submitted_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_record(cls, application: Application) -> Self:
        data = {
            field: getattr(application, field)
            for field in cls.model_fields
            if hasattr(application, field)
        }
        # Os valores das respostas não são devolvidos na listagem: só quais foram dadas.
        data["answered_questions"] = sorted(application.answers)
        return cls.model_validate(data)


class ApplicationEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    event_type: str
    from_status: ApplicationStatus | None
    to_status: ApplicationStatus | None
    actor: EventActor
    details: dict[str, Any]
    created_at: datetime


class ApplicationListFilters(BaseModel):
    status: ApplicationStatus | None = None
    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    offset: int = Field(default=0, ge=0)


class ApplicationListResponse(BaseModel):
    items: list[ApplicationOut]
    total: int
    limit: int
    offset: int


class AssistedQuotaOut(BaseModel):
    platform: str
    limit: int
    applied_today: int
    pending_kits: int
    remaining: int


class KitAnswer(BaseModel):
    field: str
    label: str
    value: str


class ApplicationKitOut(BaseModel):
    """Tudo o que você precisa para se candidatar na plataforma em poucos minutos."""

    application_id: uuid.UUID
    platform: str
    job_title: str
    company: str
    job_url: str = Field(description="Abra este link e cole os dados abaixo no formulário.")
    cover_letter: str | None
    cover_letter_warnings: list[str]
    resume_id: uuid.UUID | None
    resume_filename: str | None
    resume_download_path: str | None
    answers: list[KitAnswer] = Field(description="Respostas tiradas só do seu perfil.")
    pending_questions: list[PendingQuestionOut]
    quota: AssistedQuotaOut | None
    next_step: str
