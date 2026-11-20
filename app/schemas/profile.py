"""Contratos de entrada e saída do perfil profissional."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    HttpUrl,
    StringConstraints,
    TypeAdapter,
    model_validator,
)

from app.models.enums import (
    EducationStatus,
    LanguageProficiency,
    ResumeImportStatus,
    SkillCategory,
    SkillLevel,
    WorkModel,
)

MAX_LIST_ITEMS = 100

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
TagText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=10_000)]


_http_url_adapter = TypeAdapter(HttpUrl)


def _validate_http_url(value: str) -> str:
    return str(_http_url_adapter.validate_python(value))


# Validado como URL http(s), mas mantido como str para persistir e serializar sem conversões.
UrlText = Annotated[str, StringConstraints(max_length=500), AfterValidator(_validate_http_url)]


def _ensure_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            unique.append(value)
    return unique


TagList = Annotated[list[TagText], Field(max_length=MAX_LIST_ITEMS), AfterValidator(_ensure_unique)]
ShortTextList = Annotated[
    list[ShortText], Field(max_length=MAX_LIST_ITEMS), AfterValidator(_ensure_unique)
]


def _check_date_order(start: date | None, end: date | None) -> None:
    if start and end and end < start:
        raise ValueError("A data de término não pode ser anterior à data de início.")


class WorkExperienceIn(BaseModel):
    company: ShortText
    title: ShortText
    location: ShortText | None = None
    start_date: date
    end_date: date | None = None
    is_current: bool = False
    description: LongText | None = None
    technologies: TagList = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_dates(self) -> Self:
        _check_date_order(self.start_date, self.end_date)
        if self.is_current and self.end_date:
            raise ValueError("Uma experiência atual não pode ter data de término.")
        return self


class EducationIn(BaseModel):
    institution: ShortText
    degree: ShortText
    field_of_study: ShortText | None = None
    status: EducationStatus
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def validate_dates(self) -> Self:
        _check_date_order(self.start_date, self.end_date)
        return self


class SkillIn(BaseModel):
    name: TagText
    category: SkillCategory
    level: SkillLevel | None = None
    years_of_experience: int | None = Field(default=None, ge=0, le=60)


class LanguageIn(BaseModel):
    name: TagText
    proficiency: LanguageProficiency


class CertificationIn(BaseModel):
    name: ShortText
    issuer: ShortText | None = None
    issued_at: date | None = None
    expires_at: date | None = None
    credential_url: UrlText | None = None

    @model_validator(mode="after")
    def validate_dates(self) -> Self:
        _check_date_order(self.issued_at, self.expires_at)
        return self


class ProfileIn(BaseModel):
    """Perfil completo. O PUT substitui todo o perfil, inclusive as listas aninhadas."""

    full_name: ShortText
    contact_email: EmailStr | None = None
    phone: Annotated[str, StringConstraints(pattern=r"^\+?[0-9 ()\-]{8,20}$")] | None = None
    city: ShortText | None = None
    state: ShortText | None = None
    country: ShortText | None = None
    current_title: ShortText | None = None
    desired_titles: ShortTextList = Field(default_factory=list)
    summary: LongText | None = None
    portfolio_url: UrlText | None = None
    github_url: UrlText | None = None
    linkedin_url: UrlText | None = None
    salary_expectation_min: Decimal | None = Field(
        default=None, ge=0, max_digits=12, decimal_places=2
    )
    salary_currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] | None = None
    desired_work_models: list[WorkModel] = Field(default_factory=list, max_length=3)
    accepted_locations: ShortTextList = Field(default_factory=list)
    availability: ShortText | None = None
    restrictions: LongText | None = None
    preferences: LongText | None = None

    experiences: list[WorkExperienceIn] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)
    educations: list[EducationIn] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)
    skills: list[SkillIn] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)
    languages: list[LanguageIn] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)
    certifications: list[CertificationIn] = Field(default_factory=list, max_length=MAX_LIST_ITEMS)

    @model_validator(mode="after")
    def validate_profile(self) -> Self:
        if self.salary_expectation_min is not None and self.salary_currency is None:
            raise ValueError("Informe a moeda (salary_currency) junto com a pretensão salarial.")
        self.desired_work_models = list(dict.fromkeys(self.desired_work_models))

        skill_keys = [(skill.name.casefold(), skill.category) for skill in self.skills]
        if len(skill_keys) != len(set(skill_keys)):
            raise ValueError("Há competências repetidas na mesma categoria.")

        language_names = [language.name.casefold() for language in self.languages]
        if len(language_names) != len(set(language_names)):
            raise ValueError("Há idiomas repetidos.")
        return self


class _OrmModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class WorkExperienceOut(_OrmModel, WorkExperienceIn):
    id: uuid.UUID


class EducationOut(_OrmModel, EducationIn):
    id: uuid.UUID


class SkillOut(_OrmModel, SkillIn):
    id: uuid.UUID


class LanguageOut(_OrmModel, LanguageIn):
    id: uuid.UUID


class CertificationOut(_OrmModel, CertificationIn):
    id: uuid.UUID


class ProfileOut(_OrmModel, ProfileIn):
    id: uuid.UUID
    experiences: list[WorkExperienceOut]
    educations: list[EducationOut]
    skills: list[SkillOut]
    languages: list[LanguageOut]
    certifications: list[CertificationOut]
    created_at: datetime
    updated_at: datetime


class ResumeOut(_OrmModel):
    id: uuid.UUID
    original_filename: str
    size_bytes: int
    import_status: ResumeImportStatus
    extracted_data: dict[str, Any] | None
    extraction_error: str | None
    confirmed_at: datetime | None
    created_at: datetime


class ResumeImportConfirmation(BaseModel):
    """Perfil revisado pelo usuário a partir do rascunho extraído do currículo."""

    profile: ProfileIn
