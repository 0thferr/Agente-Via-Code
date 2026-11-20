"""Conexão de contas de e-mail (OAuth + PKCE) e monitoramento de mensagens.

O sistema só lê. Nenhum caminho do código envia e-mail ou responde recrutadores.
"""

import base64
import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.crypto import SecretCipher
from app.core.errors import IntegrationError, NotFoundError, ValidationError
from app.integrations.email.base import EmailClient, EmailMessage, EmailProviderError, OAuthTokens
from app.models.application import Application
from app.models.communication import EmailAccount, OAuthState, RecruitmentMessage
from app.models.enums import (
    ApplicationStatus,
    EmailAccountStatus,
    EmailProvider,
    EventActor,
    MatchStatus,
    MessageCategory,
    NotificationEvent,
)
from app.models.job import JobOpportunity
from app.schemas.communication import MessageFilters
from app.services.applications.state_machine import can_transition, record_event, transition
from app.services.audit_service import record_audit_event
from app.services.communication import notification_service
from app.services.communication.classifier import classify
from app.services.communication.matcher import Candidate, match_message

logger = logging.getLogger(__name__)

OAUTH_STATE_TTL = timedelta(minutes=10)
TOKEN_REFRESH_MARGIN = timedelta(minutes=2)
MAX_MESSAGES_PER_SYNC = 50
DEADLINE_WARNING_WINDOW = timedelta(hours=48)
# A caixa simulada não usa token real; este valor só preenche o campo.
SIMULATED_ACCESS_TOKEN = "simulated"  # noqa: S105

# Candidaturas que podem receber e-mails do processo seletivo.
TRACKED_STATUSES = {
    ApplicationStatus.APPLIED,
    ApplicationStatus.ASSESSMENT,
    ApplicationStatus.INTERVIEW,
    ApplicationStatus.OFFER,
}
CATEGORY_TO_STATUS = {
    MessageCategory.INTERVIEW_INVITE: ApplicationStatus.INTERVIEW,
    MessageCategory.ASSESSMENT: ApplicationStatus.ASSESSMENT,
    MessageCategory.OFFER: ApplicationStatus.OFFER,
    MessageCategory.REJECTION: ApplicationStatus.CLOSED,
}
CATEGORY_TO_EVENT = {
    MessageCategory.INTERVIEW_INVITE: (
        NotificationEvent.INTERVIEW_INVITE,
        "Convite para entrevista",
    ),
    MessageCategory.ASSESSMENT: (NotificationEvent.ASSESSMENT_RECEIVED, "Teste técnico recebido"),
    MessageCategory.OFFER: (NotificationEvent.OFFER_RECEIVED, "Proposta de emprego recebida"),
}


# --- Conexão --------------------------------------------------------------------------


def start_connection(
    session: Session,
    client: EmailClient,
    cipher: SecretCipher,
    user_id: uuid.UUID,
    redirect_uri: str,
    now: datetime,
) -> str:
    """Gera state + PKCE e devolve a URL de autorização do provedor."""
    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    session.add(
        OAuthState(
            user_id=user_id,
            provider=client.provider,
            state_hash=_hash(state),
            encrypted_code_verifier=cipher.encrypt(code_verifier),
            expires_at=now + OAUTH_STATE_TTL,
        )
    )
    session.commit()
    return client.authorization_url(
        state=state, code_challenge=challenge, redirect_uri=redirect_uri
    )


def complete_connection(
    session: Session,
    client: EmailClient,
    cipher: SecretCipher,
    *,
    state: str,
    code: str,
    redirect_uri: str,
    now: datetime,
) -> EmailAccount:
    """Valida o state (uso único, não expirado, mesmo provedor) e troca o código por tokens."""
    stored = session.scalar(
        select(OAuthState).where(OAuthState.state_hash == _hash(state)).with_for_update()
    )
    if (
        stored is None
        or stored.used_at is not None
        or stored.expires_at < now
        or stored.provider is not client.provider
    ):
        raise ValidationError("Autorização inválida ou expirada. Inicie a conexão novamente.")
    stored.used_at = now
    session.commit()  # marca como usado antes da troca: o state nunca vale duas vezes

    try:
        tokens = client.exchange_code(
            code=code,
            code_verifier=cipher.decrypt(stored.encrypted_code_verifier),
            redirect_uri=redirect_uri,
        )
        address = client.account_email(tokens.access_token)
    except EmailProviderError as exc:
        raise IntegrationError(str(exc)) from exc
    return _save_account(session, cipher, stored.user_id, client.provider, address, tokens)


def connect_simulated(
    session: Session, client: EmailClient, cipher: SecretCipher, user_id: uuid.UUID
) -> EmailAccount:
    tokens = OAuthTokens(
        access_token=SIMULATED_ACCESS_TOKEN, refresh_token=None, expires_at=None, scopes=[]
    )
    address = client.account_email(tokens.access_token)
    return _save_account(session, cipher, user_id, client.provider, address, tokens)


def _save_account(
    session: Session,
    cipher: SecretCipher,
    user_id: uuid.UUID,
    provider: EmailProvider,
    address: str,
    tokens: OAuthTokens,
) -> EmailAccount:
    account = session.scalar(
        select(EmailAccount).where(
            EmailAccount.user_id == user_id,
            EmailAccount.provider == provider,
            EmailAccount.email_address == address,
        )
    ) or EmailAccount(user_id=user_id, provider=provider, email_address=address)
    _store_tokens(account, cipher, tokens)
    account.status = EmailAccountStatus.ACTIVE
    account.last_error = None
    session.add(account)
    session.flush()
    record_audit_event(
        session,
        action="email_account.connected",
        user_id=user_id,
        entity_type="email_account",
        entity_id=account.id,
        details={"provider": provider},
    )
    session.commit()
    return account


def disconnect(
    session: Session, user_id: uuid.UUID, provider: EmailProvider, account_id: uuid.UUID | None
) -> int:
    """Remove a conta, os tokens e as mensagens associadas (minimização de dados)."""
    conditions = [EmailAccount.user_id == user_id, EmailAccount.provider == provider]
    if account_id:
        conditions.append(EmailAccount.id == account_id)
    accounts = session.scalars(select(EmailAccount).where(*conditions)).all()
    if not accounts:
        raise NotFoundError("Nenhuma conta conectada para este provedor.")
    for account in accounts:
        session.delete(account)
        record_audit_event(
            session,
            action="email_account.disconnected",
            user_id=user_id,
            entity_type="email_account",
            entity_id=account.id,
        )
    session.commit()
    return len(accounts)


def list_accounts(session: Session, user_id: uuid.UUID) -> list[EmailAccount]:
    return list(
        session.scalars(
            select(EmailAccount)
            .where(EmailAccount.user_id == user_id)
            .order_by(EmailAccount.created_at)
        )
    )


# --- Sincronização --------------------------------------------------------------------


def sync_user_accounts(
    session: Session,
    clients: dict[EmailProvider, EmailClient],
    cipher: SecretCipher,
    user_id: uuid.UUID,
    lookback: timedelta,
    now: datetime,
) -> dict[str, int]:
    totals = {"accounts": 0, "new_messages": 0, "matched": 0, "needs_review": 0, "errors": 0}
    for account in list_accounts(session, user_id):
        client = clients.get(account.provider)
        if client is None:
            continue
        totals["accounts"] += 1
        try:
            result = sync_account(session, client, cipher, account, lookback, now)
        except EmailProviderError as exc:
            account.status = EmailAccountStatus.ERROR
            account.last_error = str(exc)[:500]
            session.commit()
            totals["errors"] += 1
            continue
        for key in ("new_messages", "matched", "needs_review"):
            totals[key] += result[key]
    if totals["errors"] and totals["errors"] == totals["accounts"]:
        raise IntegrationError("Nenhuma conta de e-mail pôde ser sincronizada.")
    return totals


def sync_account(
    session: Session,
    client: EmailClient,
    cipher: SecretCipher,
    account: EmailAccount,
    lookback: timedelta,
    now: datetime,
) -> dict[str, int]:
    access_token = _valid_access_token(session, client, cipher, account, now)
    since = account.last_synced_at or (now - lookback)
    messages = client.list_messages(access_token, since, MAX_MESSAGES_PER_SYNC)
    candidates = _tracked_candidates(session, account.user_id)
    counts = {"new_messages": 0, "matched": 0, "needs_review": 0}
    for message in messages:
        stored = _store_message(session, account, message, candidates)
        if stored is None:
            continue  # já processada antes (idempotente)
        counts["new_messages"] += 1
        if stored.match_status is MatchStatus.MATCHED:
            counts["matched"] += 1
            _apply_effects(session, stored)
        elif stored.match_status is MatchStatus.NEEDS_REVIEW:
            counts["needs_review"] += 1
            notification_service.notify(
                session,
                account.user_id,
                NotificationEvent.MESSAGE_NEEDS_REVIEW,
                title="Confirme a vaga deste e-mail",
                body=f"Não temos certeza a qual candidatura pertence: {stored.subject}",
                data={"message_id": str(stored.id)},
                dedup_key=f"message_review:{stored.id}",
            )
    account.last_synced_at = now
    account.status = EmailAccountStatus.ACTIVE
    account.last_error = None
    session.commit()
    return counts


def _store_message(
    session: Session,
    account: EmailAccount,
    message: EmailMessage,
    candidates: list[Candidate],
) -> RecruitmentMessage | None:
    classification = classify(message.subject, message.snippet, message.received_at)
    if classification.category is MessageCategory.NOT_RECRUITING:
        match_status, application_id, confidence = MatchStatus.NOT_APPLICABLE, None, 0.0
    else:
        result = match_message(message.from_address, message.subject, message.snippet, candidates)
        application_id, confidence = result.application_id, result.confidence
        if application_id is None:
            match_status = MatchStatus.UNMATCHED
        elif result.ambiguous:
            match_status = MatchStatus.NEEDS_REVIEW
        else:
            match_status = MatchStatus.MATCHED

    new_id = session.scalar(
        insert(RecruitmentMessage)
        .values(
            id=uuid.uuid4(),
            user_id=account.user_id,
            email_account_id=account.id,
            provider_message_id=message.provider_message_id,
            thread_id=message.thread_id,
            from_address=message.from_address[:320],
            subject=message.subject[:300],
            snippet=message.snippet[:300],
            received_at=message.received_at,
            category=classification.category,
            requires_response=classification.requires_response,
            deadline_at=classification.deadline_at,
            detected_dates=[moment.isoformat() for moment in classification.detected_dates],
            # Em NEEDS_REVIEW o application_id é só uma sugestão: nenhum efeito é
            # aplicado até o usuário confirmar.
            application_id=application_id,
            match_status=match_status,
            match_confidence=confidence,
        )
        .on_conflict_do_nothing(constraint="uq_recruitment_messages_provider_id")
        .returning(RecruitmentMessage.id)
    )
    return session.get(RecruitmentMessage, new_id) if new_id else None


def _apply_effects(session: Session, message: RecruitmentMessage) -> None:
    """Registra o e-mail na timeline da candidatura, atualiza a etapa e notifica."""
    application = session.get(Application, message.application_id)
    if application is None:
        return
    details = {"message_id": str(message.id), "category": message.category}
    target = CATEGORY_TO_STATUS.get(message.category)
    if target and can_transition(application.status, target):
        transition(
            session,
            application,
            target,
            actor=EventActor.SYSTEM,
            event_type="email_received",
            details=details,
        )
    else:
        record_event(
            session, application, "email_received", actor=EventActor.SYSTEM, details=details
        )

    event = CATEGORY_TO_EVENT.get(message.category)
    if event:
        notification_service.notify(
            session,
            application.user_id,
            event[0],
            title=event[1],
            body=message.subject,
            data={"message_id": str(message.id), "application_id": str(application.id)},
            dedup_key=f"message:{message.id}",
        )


def _tracked_candidates(session: Session, user_id: uuid.UUID) -> list[Candidate]:
    rows = session.execute(
        select(Application.id, JobOpportunity.company, JobOpportunity.title, JobOpportunity.url)
        .join(JobOpportunity, JobOpportunity.id == Application.job_id)
        .where(Application.user_id == user_id, Application.status.in_(TRACKED_STATUSES))
    ).all()
    return [Candidate(row.id, row.company, row.title, row.url) for row in rows]


def _valid_access_token(
    session: Session,
    client: EmailClient,
    cipher: SecretCipher,
    account: EmailAccount,
    now: datetime,
) -> str:
    if not client.uses_oauth:
        return SIMULATED_ACCESS_TOKEN
    expired = account.token_expires_at and account.token_expires_at - TOKEN_REFRESH_MARGIN <= now
    if expired:
        if not account.encrypted_refresh_token:
            raise EmailProviderError("Token expirado e sem refresh token; reconecte a conta.")
        tokens = client.refresh(cipher.decrypt(account.encrypted_refresh_token))
        _store_tokens(account, cipher, tokens)
        session.commit()
    if not account.encrypted_access_token:
        raise EmailProviderError("Conta sem token de acesso; reconecte a conta.")
    return cipher.decrypt(account.encrypted_access_token)


def _store_tokens(account: EmailAccount, cipher: SecretCipher, tokens: OAuthTokens) -> None:
    account.encrypted_access_token = cipher.encrypt(tokens.access_token)
    if tokens.refresh_token:
        account.encrypted_refresh_token = cipher.encrypt(tokens.refresh_token)
    account.token_expires_at = tokens.expires_at
    account.scopes = tokens.scopes


# --- Mensagens ------------------------------------------------------------------------


def list_messages(
    session: Session, user_id: uuid.UUID, filters: MessageFilters
) -> tuple[list[RecruitmentMessage], int]:
    conditions = [RecruitmentMessage.user_id == user_id]
    if filters.category:
        conditions.append(RecruitmentMessage.category == filters.category)
    if filters.match_status:
        conditions.append(RecruitmentMessage.match_status == filters.match_status)
    if filters.application_id:
        conditions.append(RecruitmentMessage.application_id == filters.application_id)
    total = session.scalar(select(func.count()).select_from(RecruitmentMessage).where(*conditions))
    items = session.scalars(
        select(RecruitmentMessage)
        .where(*conditions)
        .order_by(RecruitmentMessage.received_at.desc())
        .limit(filters.limit)
        .offset(filters.offset)
    )
    return list(items), total or 0


def set_association(
    session: Session, user_id: uuid.UUID, message_id: uuid.UUID, application_id: uuid.UUID | None
) -> RecruitmentMessage:
    """Validação do usuário: confirma a candidatura certa (ou nenhuma)."""
    message = session.scalar(
        select(RecruitmentMessage).where(
            RecruitmentMessage.id == message_id, RecruitmentMessage.user_id == user_id
        )
    )
    if message is None:
        raise NotFoundError("Mensagem não encontrada.")
    if application_id is None:
        message.application_id = None
        message.match_status = MatchStatus.UNMATCHED
        session.commit()
        return message

    application = session.scalar(
        select(Application).where(Application.id == application_id, Application.user_id == user_id)
    )
    if application is None:
        raise NotFoundError("Candidatura não encontrada.")
    already_applied = (
        message.match_status is MatchStatus.MATCHED and message.application_id == application_id
    )
    message.application_id = application_id
    message.match_status = MatchStatus.MATCHED
    message.match_confidence = 1.0
    if not already_applied:
        _apply_effects(session, message)
    session.commit()
    return message


def notify_upcoming_deadlines(session: Session, user_id: uuid.UUID, now: datetime) -> int:
    messages = session.scalars(
        select(RecruitmentMessage).where(
            RecruitmentMessage.user_id == user_id,
            RecruitmentMessage.deadline_at > now,
            RecruitmentMessage.deadline_at <= now + DEADLINE_WARNING_WINDOW,
        )
    ).all()
    created = 0
    for message in messages:
        notification = notification_service.notify(
            session,
            user_id,
            NotificationEvent.DEADLINE_APPROACHING,
            title="Prazo próximo do vencimento",
            body=f"{message.subject} — prazo: {message.deadline_at:%d/%m %H:%M} (UTC)",
            data={"message_id": str(message.id)},
            dedup_key=f"deadline:{message.id}",
            now=now,
        )
        created += notification is not None
    session.commit()
    return created


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
