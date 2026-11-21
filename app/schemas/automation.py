"""Contratos das regras de automação e do histórico de execuções."""

import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.core.timezones import DEFAULT_TIMEZONE, validate_timezone
from app.models.enums import AutomationTask, JobRunStatus
from app.schemas.job import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, JobSearchFilters

MIN_INTERVAL_MINUTES = 15
MAX_ITEMS_PER_RUN = 50
DEFAULT_ITEMS_PER_RUN = 10
TIME_OF_DAY_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class DiscoverJobsConfig(BaseModel):
    filters: JobSearchFilters = Field(default_factory=JobSearchFilters)
    source_ids: list[uuid.UUID] | None = None


class BatchConfig(BaseModel):
    max_items: int = Field(default=DEFAULT_ITEMS_PER_RUN, ge=1, le=MAX_ITEMS_PER_RUN)


class EmptyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


# Cada tipo de tarefa valida a sua própria configuração.
TASK_CONFIG_MODELS: dict[AutomationTask, type[BaseModel]] = {
    AutomationTask.DISCOVER_JOBS: DiscoverJobsConfig,
    AutomationTask.ANALYZE_JOBS: BatchConfig,
    AutomationTask.PREPARE_APPLICATIONS: BatchConfig,
    AutomationTask.SUBMIT_APPLICATIONS: BatchConfig,
    AutomationTask.CHECK_EMAILS: EmptyConfig,
    AutomationTask.CHECK_DEADLINES: EmptyConfig,
    AutomationTask.DAILY_SUMMARY: EmptyConfig,
}


class ScheduleFields(BaseModel):
    days_of_week: list[Annotated[int, Field(ge=0, le=6)]] = Field(
        default_factory=list,
        max_length=7,
        description="0 = segunda ... 6 = domingo; vazio = todos.",
    )
    time_of_day: str | None = Field(default=None, description='Horário "HH:MM" no fuso da regra.')
    timezone: str = DEFAULT_TIMEZONE
    interval_minutes: int | None = Field(default=None, ge=MIN_INTERVAL_MINUTES, le=7 * 24 * 60)

    @field_validator("time_of_day")
    @classmethod
    def validate_time(cls, value: str | None) -> str | None:
        if value is not None and not TIME_OF_DAY_PATTERN.match(value):
            raise ValueError('Use o formato "HH:MM" (24h).')
        return value

    @field_validator("timezone")
    @classmethod
    def check_timezone(cls, value: str) -> str:
        return validate_timezone(value)


class AutomationRuleCreate(ScheduleFields):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    task_type: AutomationTask
    is_enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_rule(self) -> Self:
        if (self.time_of_day is None) == (self.interval_minutes is None):
            raise ValueError("Informe exatamente um: time_of_day ou interval_minutes.")
        self.config = (
            TASK_CONFIG_MODELS[self.task_type].model_validate(self.config).model_dump(mode="json")
        )
        self.days_of_week = sorted(set(self.days_of_week))
        return self


class AutomationRuleUpdate(BaseModel):
    """Atualização parcial. O agendamento é revalidado com os valores resultantes."""

    name: (
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
        | None
    ) = None
    is_enabled: bool | None = None
    days_of_week: list[Annotated[int, Field(ge=0, le=6)]] | None = Field(default=None, max_length=7)
    time_of_day: str | None = None
    timezone: str | None = None
    interval_minutes: int | None = None
    config: dict[str, Any] | None = None


class AutomationRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    task_type: AutomationTask
    is_enabled: bool
    days_of_week: list[int]
    time_of_day: str | None
    timezone: str
    interval_minutes: int | None
    config: dict[str, Any]
    next_run_at: datetime | None
    last_run_at: datetime | None
    created_at: datetime


class ExecutionLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    attempt: int
    status: JobRunStatus
    summary: dict[str, Any]
    error: str | None
    started_at: datetime
    finished_at: datetime


class ScheduledJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rule_id: uuid.UUID
    task_type: AutomationTask
    scheduled_for: datetime
    status: JobRunStatus
    attempts: int
    max_attempts: int
    next_attempt_at: datetime
    last_error: str | None
    finished_at: datetime | None
    logs: list[ExecutionLogOut] = Field(default_factory=list)


class ExecutionFilters(BaseModel):
    rule_id: uuid.UUID | None = None
    status: JobRunStatus | None = None
    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    offset: int = Field(default=0, ge=0)


class ExecutionListResponse(BaseModel):
    items: list[ScheduledJobOut]
    total: int
    limit: int
    offset: int
