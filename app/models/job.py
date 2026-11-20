import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import EMPTY_ARRAY, text_enum
from app.models.enums import DiscoveryMethod, Seniority, WorkModel


class JobSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Fonte de vagas habilitada por um usuário (aponta para um provedor do catálogo)."""

    __tablename__ = "job_sources"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider_key: Mapped[str] = mapped_column(String(50), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("user_id", "provider_key", name="uq_job_sources_user_provider"),
    )


class JobOpportunity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Vaga normalizada. Três chaves impedem duplicidade para o mesmo usuário:

    - (provider_key, external_id): o mesmo anúncio na mesma fonte;
    - url_hash: a mesma URL canônica (sem parâmetros de rastreamento);
    - content_fingerprint: empresa + cargo + local iguais, mesmo vindo de outra fonte.
    """

    __tablename__ = "job_opportunities"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("job_sources.id", ondelete="SET NULL")
    )
    provider_key: Mapped[str] = mapped_column(String(50), nullable=False)
    discovery_method: Mapped[DiscoveryMethod] = mapped_column(
        text_enum(DiscoveryMethod), nullable=False
    )
    external_id: Mapped[str | None] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(String(2000), nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(2000), nullable=False)
    url_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    company: Mapped[str] = mapped_column(String(200), nullable=False)
    location: Mapped[str | None] = mapped_column(String(200))
    work_model: Mapped[WorkModel | None] = mapped_column(text_enum(WorkModel))
    seniority: Mapped[Seniority | None] = mapped_column(text_enum(Seniority))
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    requirements: Mapped[list[str]] = mapped_column(
        ARRAY(String(300)), default=list, server_default=EMPTY_ARRAY
    )
    technologies: Mapped[list[str]] = mapped_column(
        ARRAY(String(100)), default=list, server_default=EMPTY_ARRAY
    )
    salary_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    salary_max: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    salary_currency: Mapped[str | None] = mapped_column(String(3))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("user_id", "provider_key", "external_id", name="uq_jobs_user_external"),
        UniqueConstraint("user_id", "url_hash", name="uq_jobs_user_url_hash"),
        UniqueConstraint("user_id", "content_fingerprint", name="uq_jobs_user_fingerprint"),
        CheckConstraint(
            "salary_min IS NULL OR salary_max IS NULL OR salary_max >= salary_min",
            name="salary_range_ordered",
        ),
        Index("ix_jobs_user_posted_at", "user_id", "posted_at"),
        Index("ix_jobs_user_company", "user_id", "company"),
        Index("ix_jobs_user_created", "user_id", "created_at"),
    )
