"""Importa todos os modelos para registrá-los no metadata (usado pelo Alembic)."""

from app.models.analysis import AIPreferences, CoverLetter, JobAnalysis
from app.models.application import (
    Application,
    ApplicationApproval,
    ApplicationEvent,
    ApplicationSettings,
)
from app.models.audit import AuditLog
from app.models.job import JobOpportunity, JobSource
from app.models.profile import (
    Certification,
    Education,
    Language,
    ProfessionalProfile,
    Resume,
    Skill,
    WorkExperience,
)
from app.models.user import RefreshToken, User

__all__ = [
    "AIPreferences",
    "Application",
    "ApplicationApproval",
    "ApplicationEvent",
    "ApplicationSettings",
    "AuditLog",
    "Certification",
    "CoverLetter",
    "Education",
    "JobAnalysis",
    "JobOpportunity",
    "JobSource",
    "Language",
    "ProfessionalProfile",
    "RefreshToken",
    "Resume",
    "Skill",
    "User",
    "WorkExperience",
]
