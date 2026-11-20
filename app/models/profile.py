import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.db.types import EMPTY_ARRAY, text_enum
from app.models.enums import (
    EducationStatus,
    LanguageProficiency,
    ResumeImportStatus,
    Seniority,
    SkillCategory,
    SkillLevel,
)

if TYPE_CHECKING:
    from app.models.user import User


class ProfessionalProfile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Perfil profissional real e validado pelo próprio usuário (1:1 com User)."""

    __tablename__ = "professional_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    contact_email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(32))
    city: Mapped[str | None] = mapped_column(String(120))
    state: Mapped[str | None] = mapped_column(String(120))
    country: Mapped[str | None] = mapped_column(String(120))
    current_title: Mapped[str | None] = mapped_column(String(200))
    # Senioridade declarada pelo próprio candidato (não é deduzida automaticamente).
    seniority: Mapped[Seniority | None] = mapped_column(text_enum(Seniority))
    desired_titles: Mapped[list[str]] = mapped_column(
        ARRAY(String(200)), default=list, server_default=EMPTY_ARRAY
    )
    summary: Mapped[str | None] = mapped_column(Text)
    portfolio_url: Mapped[str | None] = mapped_column(String(500))
    github_url: Mapped[str | None] = mapped_column(String(500))
    linkedin_url: Mapped[str | None] = mapped_column(String(500))
    salary_expectation_min: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    salary_currency: Mapped[str | None] = mapped_column(String(3))
    desired_work_models: Mapped[list[str]] = mapped_column(
        ARRAY(String(16)), default=list, server_default=EMPTY_ARRAY
    )
    accepted_locations: Mapped[list[str]] = mapped_column(
        ARRAY(String(200)), default=list, server_default=EMPTY_ARRAY
    )
    availability: Mapped[str | None] = mapped_column(String(200))
    restrictions: Mapped[str | None] = mapped_column(Text)
    preferences: Mapped[str | None] = mapped_column(Text)

    user: Mapped["User"] = relationship(back_populates="profile")
    experiences: Mapped[list["WorkExperience"]] = relationship(
        cascade="all, delete-orphan", order_by="WorkExperience.start_date.desc()"
    )
    educations: Mapped[list["Education"]] = relationship(
        cascade="all, delete-orphan", order_by="Education.start_date.desc()"
    )
    skills: Mapped[list["Skill"]] = relationship(
        cascade="all, delete-orphan", order_by="Skill.name"
    )
    languages: Mapped[list["Language"]] = relationship(
        cascade="all, delete-orphan", order_by="Language.name"
    )
    certifications: Mapped[list["Certification"]] = relationship(
        cascade="all, delete-orphan", order_by="Certification.name"
    )

    __table_args__ = (
        CheckConstraint(
            "salary_expectation_min IS NULL OR salary_expectation_min >= 0",
            name="salary_non_negative",
        ),
    )


class _ProfileChild(UUIDPrimaryKeyMixin):
    """Colunas comuns aos itens que pertencem a um perfil."""

    profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("professional_profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )


class WorkExperience(_ProfileChild, Base):
    __tablename__ = "work_experiences"

    company: Mapped[str] = mapped_column(String(200), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    location: Mapped[str | None] = mapped_column(String(200))
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    technologies: Mapped[list[str]] = mapped_column(
        ARRAY(String(100)), default=list, server_default=EMPTY_ARRAY
    )

    __table_args__ = (
        CheckConstraint(
            "end_date IS NULL OR end_date >= start_date", name="experience_dates_ordered"
        ),
    )


class Education(_ProfileChild, Base):
    __tablename__ = "educations"

    institution: Mapped[str] = mapped_column(String(200), nullable=False)
    degree: Mapped[str] = mapped_column(String(200), nullable=False)
    field_of_study: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[EducationStatus] = mapped_column(text_enum(EducationStatus), nullable=False)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)

    __table_args__ = (
        CheckConstraint(
            "end_date IS NULL OR start_date IS NULL OR end_date >= start_date",
            name="education_dates_ordered",
        ),
    )


class Skill(_ProfileChild, Base):
    __tablename__ = "skills"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    category: Mapped[SkillCategory] = mapped_column(text_enum(SkillCategory), nullable=False)
    level: Mapped[SkillLevel | None] = mapped_column(text_enum(SkillLevel))
    years_of_experience: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint("profile_id", "name", "category", name="uq_skills_profile_name_category"),
        CheckConstraint(
            "years_of_experience IS NULL OR years_of_experience >= 0", name="years_non_negative"
        ),
    )


class Language(_ProfileChild, Base):
    __tablename__ = "languages"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    proficiency: Mapped[LanguageProficiency] = mapped_column(
        text_enum(LanguageProficiency), nullable=False
    )

    __table_args__ = (UniqueConstraint("profile_id", "name", name="uq_languages_profile_name"),)


class Certification(_ProfileChild, Base):
    __tablename__ = "certifications"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    issuer: Mapped[str | None] = mapped_column(String(200))
    issued_at: Mapped[date | None] = mapped_column(Date)
    expires_at: Mapped[date | None] = mapped_column(Date)
    credential_url: Mapped[str | None] = mapped_column(String(500))


class Resume(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Currículo em PDF enviado pelo usuário e o rascunho extraído dele.

    O rascunho (`extracted_data`) só vira perfil depois da confirmação explícita
    do usuário — a IA nunca usa dados que não passaram por essa validação.
    """

    __tablename__ = "resumes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    import_status: Mapped[ResumeImportStatus] = mapped_column(
        text_enum(ResumeImportStatus), nullable=False
    )
    extracted_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    extraction_error: Mapped[str | None] = mapped_column(String(500))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("user_id", "sha256", name="uq_resumes_user_sha256"),
        Index("ix_resumes_user_id", "user_id"),
    )
