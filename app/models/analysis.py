import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import EMPTY_ARRAY, text_enum
from app.models.enums import (
    AnalysisStatus,
    CoverLetterSource,
    CoverLetterTone,
    Recommendation,
)


class AIPreferences(TimestampMixin, Base):
    """Preferências do usuário para análise e geração de textos (1:1 com User)."""

    __tablename__ = "ai_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    min_score: Mapped[int] = mapped_column(Integer, nullable=False)
    weights: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)
    cover_letter_tone: Mapped[CoverLetterTone] = mapped_column(
        text_enum(CoverLetterTone), nullable=False
    )
    writing_samples: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list, server_default=EMPTY_ARRAY
    )

    __table_args__ = (CheckConstraint("min_score BETWEEN 0 AND 100", name="min_score_range"),)


class JobAnalysis(UUIDPrimaryKeyMixin, Base):
    """Resultado de uma análise de compatibilidade. Imutável: cada nova análise
    gera um registro, preservando o histórico e os motivos de cada decisão."""

    __tablename__ = "job_analyses"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job_opportunities.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[AnalysisStatus] = mapped_column(text_enum(AnalysisStatus), nullable=False)
    recommendation: Mapped[Recommendation] = mapped_column(
        text_enum(Recommendation), nullable=False
    )
    score: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    min_score: Mapped[int] = mapped_column(Integer, nullable=False)
    weights: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)
    criteria: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    requirements: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    decision_reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    warnings: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    ai_summary: Mapped[str | None] = mapped_column(Text)
    analyzer_provider: Mapped[str] = mapped_column(String(50), nullable=False)
    analyzer_model: Mapped[str] = mapped_column(String(100), nullable=False)
    profile_version: Mapped[str] = mapped_column(String(64), nullable=False)
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("score IS NULL OR score BETWEEN 0 AND 100", name="score_range"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="confidence_range"),
        Index("ix_job_analyses_job_created", "job_id", "created_at"),
        Index("ix_job_analyses_user_created", "user_id", "created_at"),
    )


class CoverLetter(UUIDPrimaryKeyMixin, Base):
    """Versão de um texto de apresentação. Edições criam novas versões."""

    __tablename__ = "cover_letters"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job_opportunities.id", ondelete="CASCADE"), nullable=False
    )
    analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("job_analyses.id", ondelete="SET NULL")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[CoverLetterSource] = mapped_column(text_enum(CoverLetterSource), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tone: Mapped[CoverLetterTone] = mapped_column(text_enum(CoverLetterTone), nullable=False)
    max_characters: Mapped[int] = mapped_column(Integer, nullable=False)
    validation_warnings: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    generator_provider: Mapped[str | None] = mapped_column(String(50))
    generator_model: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("job_id", "version", name="uq_cover_letters_job_version"),
        CheckConstraint("max_characters > 0", name="max_characters_positive"),
        Index("ix_cover_letters_user_id", "user_id"),
    )
