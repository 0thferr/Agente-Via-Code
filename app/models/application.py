import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import EMPTY_ARRAY, text_enum
from app.models.enums import (
    ApplicationMode,
    ApplicationStatus,
    ApprovalDecision,
    ConfirmationSource,
    EventActor,
)

DEFAULT_MAX_DAILY_SUBMISSIONS = 10
DEFAULT_ASSISTED_DAILY_LIMITS = {"linkedin": 10, "gupy": 10}


class ApplicationSettings(TimestampMixin, Base):
    """Política de candidaturas do usuário (1:1 com User)."""

    __tablename__ = "application_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    mode: Mapped[ApplicationMode] = mapped_column(text_enum(ApplicationMode), nullable=False)
    # Lista explícita de provedores em que o modo AUTOMATIC pode enviar sem aprovação.
    auto_submit_providers: Mapped[list[str]] = mapped_column(
        ARRAY(String(50)), default=list, server_default=EMPTY_ARRAY
    )
    max_daily_submissions: Mapped[int] = mapped_column(Integer, nullable=False)
    # Modo assistido: plataformas sem envio integrado (ex.: LinkedIn, Gupy), em que a
    # API prepara o kit e o usuário envia. Limite diário por plataforma.
    assisted_daily_limits: Mapped[dict[str, int]] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: dict(DEFAULT_ASSISTED_DAILY_LIMITS),
        server_default=text("""'{"linkedin": 10, "gupy": 10}'::jsonb"""),
    )

    __table_args__ = (CheckConstraint("max_daily_submissions >= 0", name="max_daily_non_negative"),)


class Application(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Candidatura a uma vaga. Existe no máximo uma por vaga e usuário."""

    __tablename__ = "applications"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job_opportunities.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[ApplicationStatus] = mapped_column(text_enum(ApplicationStatus), nullable=False)
    analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("job_analyses.id", ondelete="SET NULL")
    )
    cover_letter_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cover_letters.id", ondelete="SET NULL")
    )
    resume_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resumes.id", ondelete="SET NULL")
    )
    compatibility_score: Mapped[int | None] = mapped_column(Integer)
    compatibility_summary: Mapped[str | None] = mapped_column(Text)
    met_requirements: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    resume_highlights: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    pending_questions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    answers: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, default=dict)

    # Chave de idempotência do envio: gerada uma vez e reutilizada em toda tentativa,
    # para que a plataforma reconheça reenvios e não crie candidaturas duplicadas.
    submission_key: Mapped[uuid.UUID | None] = mapped_column(unique=True)
    submission_channel: Mapped[str | None] = mapped_column(String(50))
    confirmation_source: Mapped[ConfirmationSource | None] = mapped_column(
        text_enum(ConfirmationSource)
    )
    external_confirmation_id: Mapped[str | None] = mapped_column(String(200))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))

    # Controle otimista de concorrência: atualizações simultâneas da mesma linha
    # falham (StaleDataError) em vez de uma sobrescrever a outra silenciosamente.
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    __mapper_args__ = {"version_id_col": row_version}

    __table_args__ = (
        UniqueConstraint("user_id", "job_id", name="uq_applications_user_job"),
        Index("ix_applications_user_status", "user_id", "status"),
        Index("ix_applications_user_submitted", "user_id", "submitted_at"),
    )


class ApplicationEvent(UUIDPrimaryKeyMixin, Base):
    """Linha do tempo imutável da candidatura (transições e ações relevantes)."""

    __tablename__ = "application_events"

    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=False
    )
    # Ordem garantida dos eventos: vários eventos da mesma transação têm o mesmo
    # created_at (now() é fixo por transação no PostgreSQL), então ordenamos por aqui.
    sequence: Mapped[int] = mapped_column(BigInteger, Identity(), nullable=False, unique=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    from_status: Mapped[ApplicationStatus | None] = mapped_column(text_enum(ApplicationStatus))
    to_status: Mapped[ApplicationStatus | None] = mapped_column(text_enum(ApplicationStatus))
    actor: Mapped[EventActor] = mapped_column(text_enum(EventActor), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_application_events_app_sequence", "application_id", "sequence"),
        # Indicadores do dashboard: "candidaturas que chegaram à etapa X no período".
        Index("ix_application_events_status_created", "to_status", "created_at"),
    )


class ApplicationApproval(UUIDPrimaryKeyMixin, Base):
    """Decisão do usuário sobre o envio, vinculada às versões exatas revisadas."""

    __tablename__ = "application_approvals"

    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), nullable=False, index=True
    )
    decision: Mapped[ApprovalDecision] = mapped_column(text_enum(ApprovalDecision), nullable=False)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    cover_letter_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cover_letters.id", ondelete="SET NULL")
    )
    resume_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("resumes.id", ondelete="SET NULL")
    )
    acknowledged_warnings: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    comment: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
