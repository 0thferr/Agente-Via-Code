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


class FitStatus(StrEnum):
    MATCH = "match"
    PARTIAL = "partial"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


class AnalysisStatus(StrEnum):
    COMPLETED = "completed"
    PENDING_INFORMATION = "pending_information"  # "Análise pendente": descrição insuficiente
    FAILED = "failed"


class Recommendation(StrEnum):
    QUALIFIED = "qualified"
    REJECTED = "rejected"
    PENDING = "pending"


class RequirementStatus(StrEnum):
    MET = "met"
    PARTIALLY_MET = "partially_met"
    NOT_MET = "not_met"
    UNCLEAR = "unclear"


class CoverLetterTone(StrEnum):
    FORMAL = "formal"
    NEUTRAL = "neutral"
    FRIENDLY = "friendly"


class CoverLetterSource(StrEnum):
    GENERATED = "generated"
    USER_EDIT = "user_edit"


class ApplicationStatus(StrEnum):
    DISCOVERED = "discovered"
    ANALYZING = "analyzing"
    REJECTED = "rejected"
    QUALIFIED = "qualified"
    AWAITING_APPROVAL = "awaiting_approval"
    READY_TO_APPLY = "ready_to_apply"
    APPLYING = "applying"
    APPLIED = "applied"
    ASSESSMENT = "assessment"
    INTERVIEW = "interview"
    OFFER = "offer"
    HIRED = "hired"
    CLOSED = "closed"
    ERROR = "error"


class ApplicationMode(StrEnum):
    MANUAL = "manual"  # o usuário analisa e envia todas as candidaturas
    SUPERVISED = "supervised"  # o agente prepara; o envio exige aprovação
    AUTOMATIC = "automatic"  # envio sem aprovação, só sob regras explícitas


class EventActor(StrEnum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class ConfirmationSource(StrEnum):
    PLATFORM = "platform"  # confirmação devolvida por integração real
    SIMULATED = "simulated"  # confirmação de integração simulada (desenvolvimento)
    USER_DECLARED = "user_declared"  # o usuário se candidatou fora do sistema


class AutomationTask(StrEnum):
    DISCOVER_JOBS = "discover_jobs"
    ANALYZE_JOBS = "analyze_jobs"
    PREPARE_APPLICATIONS = "prepare_applications"
    SUBMIT_APPLICATIONS = "submit_applications"
    CHECK_EMAILS = "check_emails"
    CHECK_DEADLINES = "check_deadlines"
    DAILY_SUMMARY = "daily_summary"


class JobRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"  # esgotou as tentativas


class EmailProvider(StrEnum):
    GMAIL = "gmail"
    OUTLOOK = "outlook"
    SIMULATED = "simulated"


class EmailAccountStatus(StrEnum):
    ACTIVE = "active"
    ERROR = "error"


class MessageCategory(StrEnum):
    INTERVIEW_INVITE = "interview_invite"
    ASSESSMENT = "assessment"
    STAGE_CHANGE = "stage_change"
    OFFER = "offer"
    REJECTION = "rejection"
    OTHER_RECRUITING = "other_recruiting"
    NOT_RECRUITING = "not_recruiting"


class MatchStatus(StrEnum):
    MATCHED = "matched"
    NEEDS_REVIEW = "needs_review"  # associação incerta: o usuário precisa validar
    UNMATCHED = "unmatched"
    NOT_APPLICABLE = "not_applicable"  # mensagem não é de recrutamento


class NotificationEvent(StrEnum):
    HIGH_MATCH_JOB = "high_match_job"
    APPROVAL_PENDING = "approval_pending"
    APPLICATION_SUBMITTED = "application_submitted"
    SUBMISSION_FAILED = "submission_failed"
    INTERVIEW_INVITE = "interview_invite"
    ASSESSMENT_RECEIVED = "assessment_received"
    OFFER_RECEIVED = "offer_received"
    MESSAGE_NEEDS_REVIEW = "message_needs_review"
    DEADLINE_APPROACHING = "deadline_approaching"
    DAILY_SUMMARY = "daily_summary"


class NotificationChannel(StrEnum):
    EMAIL = "email"
    TELEGRAM = "telegram"
    WEBHOOK = "webhook"
    PUSH = "push"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"
