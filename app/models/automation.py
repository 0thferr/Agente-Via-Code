import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import EMPTY_ARRAY, text_enum
from app.models.enums import AutomationTask, JobRunStatus


class AutomationRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Regra configurada pelo usuário: qual tarefa rodar, quando e com quais parâmetros.

    O agendamento é por horário fixo (dias da semana + hora + fuso) ou por intervalo.
    """

    __tablename__ = "automation_rules"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    task_type: Mapped[AutomationTask] = mapped_column(text_enum(AutomationTask), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    days_of_week: Mapped[list[int]] = mapped_column(
        ARRAY(SmallInteger), default=list, server_default=EMPTY_ARRAY
    )
    time_of_day: Mapped[str | None] = mapped_column(String(5))  # "HH:MM", no fuso da regra
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    interval_minutes: Mapped[int | None] = mapped_column(Integer)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "(time_of_day IS NULL) <> (interval_minutes IS NULL)", name="one_schedule_kind"
        ),
        Index("ix_automation_rules_due", "is_enabled", "next_run_at"),
        Index("ix_automation_rules_user_id", "user_id"),
    )


class ScheduledJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Uma execução lógica de uma regra.

    A constraint única (rule_id, scheduled_for) garante que, mesmo com várias
    instâncias do worker, a mesma execução lógica só exista uma vez. A "lease"
    (lease_expires_at) permite recuperar a execução se o worker morrer no meio.
    """

    __tablename__ = "scheduled_jobs"

    rule_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("automation_rules.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    task_type: Mapped[AutomationTask] = mapped_column(text_enum(AutomationTask), nullable=False)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[JobRunStatus] = mapped_column(text_enum(JobRunStatus), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(100))
    last_error: Mapped[str | None] = mapped_column(String(500))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    logs: Mapped[list["ExecutionLog"]] = relationship(
        order_by="ExecutionLog.attempt", lazy="selectin", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("rule_id", "scheduled_for", name="uq_scheduled_jobs_rule_slot"),
        Index("ix_scheduled_jobs_claim", "status", "next_attempt_at"),
        Index("ix_scheduled_jobs_user_created", "user_id", "created_at"),
    )


class ExecutionLog(UUIDPrimaryKeyMixin, Base):
    """Registro de cada tentativa de execução (histórico do agente)."""

    __tablename__ = "execution_logs"

    scheduled_job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("scheduled_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    task_type: Mapped[AutomationTask] = mapped_column(text_enum(AutomationTask), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[JobRunStatus] = mapped_column(text_enum(JobRunStatus), nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(String(500))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_execution_logs_user_started", "user_id", "started_at"),)
