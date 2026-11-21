"""Importa todos os modelos para registrá-los no metadata (usado pelo Alembic)."""

from app.models.analysis import AIPreferences, CoverLetter, JobAnalysis
from app.models.application import (
    Application,
    ApplicationApproval,
    ApplicationEvent,
    ApplicationSettings,
)
from app.models.audit import AuditLog
from app.models.automation import AutomationRule, ExecutionLog, ScheduledJob
from app.models.communication import (
    EmailAccount,
    Notification,
    NotificationDelivery,
    NotificationPreferences,
    OAuthState,
    RecruitmentMessage,
)
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
    "AutomationRule",
    "Certification",
    "CoverLetter",
    "Education",
    "EmailAccount",
    "ExecutionLog",
    "JobAnalysis",
    "JobOpportunity",
    "JobSource",
    "Language",
    "Notification",
    "NotificationDelivery",
    "NotificationPreferences",
    "OAuthState",
    "ProfessionalProfile",
    "RecruitmentMessage",
    "RefreshToken",
    "Resume",
    "ScheduledJob",
    "Skill",
    "User",
    "WorkExperience",
]
