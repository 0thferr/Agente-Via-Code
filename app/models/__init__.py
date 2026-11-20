"""Importa todos os modelos para registrá-los no metadata (usado pelo Alembic)."""

from app.models.audit import AuditLog
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
    "AuditLog",
    "Certification",
    "Education",
    "Language",
    "ProfessionalProfile",
    "RefreshToken",
    "Resume",
    "Skill",
    "User",
    "WorkExperience",
]
