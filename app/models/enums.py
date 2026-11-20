"""Enumerações de domínio compartilhadas por modelos e schemas.

São armazenadas como texto no banco (native_enum=False) para que adicionar um
valor novo não exija ALTER TYPE no PostgreSQL.
"""

from enum import StrEnum


class WorkModel(StrEnum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"


class SkillCategory(StrEnum):
    TECHNICAL = "technical"
    BEHAVIORAL = "behavioral"
    TECHNOLOGY = "technology"


class SkillLevel(StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"


class LanguageProficiency(StrEnum):
    BASIC = "basic"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    FLUENT = "fluent"
    NATIVE = "native"


class EducationStatus(StrEnum):
    COMPLETED = "completed"
    IN_PROGRESS = "in_progress"
    INCOMPLETE = "incomplete"


class ResumeImportStatus(StrEnum):
    PENDING_REVIEW = "pending_review"
    CONFIRMED = "confirmed"
    DISCARDED = "discarded"
    FAILED = "failed"


class Seniority(StrEnum):
    INTERN = "intern"
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    LEAD = "lead"
    SPECIALIST = "specialist"


class DiscoveryMethod(StrEnum):
    AUTOMATIC = "automatic"
    MANUAL = "manual"
