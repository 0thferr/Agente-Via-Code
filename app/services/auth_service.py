import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AuthenticationError, ConflictError
from app.core.security import (
    InvalidTokenError,
    TokenType,
    create_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.user import RefreshToken, User
from app.schemas.auth import TokenResponse
from app.services.audit_service import record_audit_event

INVALID_CREDENTIALS_MESSAGE = "E-mail ou senha inválidos."
MIN_PASSWORD_LENGTH = 12

# Hash calculado uma vez para igualar o tempo de resposta quando o e-mail não existe,
# evitando que um atacante descubra quais e-mails estão cadastrados.
_DUMMY_PASSWORD_HASH = hash_password("dummy-password-for-timing")


def normalize_email(email: str) -> str:
    return email.strip().lower()


def create_user(session: Session, email: str, password: str) -> User:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"A senha precisa ter pelo menos {MIN_PASSWORD_LENGTH} caracteres.")
    normalized = normalize_email(email)
    if session.scalar(select(User).where(User.email == normalized)):
        raise ConflictError("Já existe um usuário com este e-mail.")

    user = User(email=normalized, password_hash=hash_password(password))
    session.add(user)
    session.flush()
    record_audit_event(
        session, action="user.created", user_id=user.id, entity_type="user", entity_id=user.id
    )
    session.commit()
    return user


def authenticate(session: Session, email: str, password: str, settings: Settings) -> TokenResponse:
    user = session.scalar(select(User).where(User.email == normalize_email(email)))
    if user is None:
        verify_password(password, _DUMMY_PASSWORD_HASH)
        raise AuthenticationError(INVALID_CREDENTIALS_MESSAGE)
    if not verify_password(password, user.password_hash) or not user.is_active:
        record_audit_event(session, action="auth.login_failed", user_id=user.id)
        session.commit()
        raise AuthenticationError(INVALID_CREDENTIALS_MESSAGE)

    tokens = _issue_tokens(session, user.id, settings)
    record_audit_event(session, action="auth.login", user_id=user.id)
    session.commit()
    return tokens


def refresh_tokens(session: Session, refresh_token: str, settings: Settings) -> TokenResponse:
    """Troca um refresh token válido por um novo par (rotação).

    Se um token já revogado for reutilizado, assumimos que ele vazou e revogamos
    todos os refresh tokens do usuário.
    """
    try:
        payload = decode_token(refresh_token, TokenType.REFRESH, settings)
    except InvalidTokenError as exc:
        raise AuthenticationError("Refresh token inválido.") from exc

    stored = session.get(RefreshToken, payload.token_id, with_for_update=True)
    if stored is None or stored.user_id != payload.user_id:
        raise AuthenticationError("Refresh token inválido.")

    if stored.revoked_at is not None:
        _revoke_all_refresh_tokens(session, payload.user_id)
        record_audit_event(session, action="auth.refresh_token_reuse", user_id=payload.user_id)
        session.commit()
        raise AuthenticationError("Refresh token inválido.")

    user = session.get(User, payload.user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Refresh token inválido.")

    stored.revoked_at = datetime.now(UTC)
    tokens = _issue_tokens(session, user.id, settings)
    session.commit()
    return tokens


def get_user_from_access_token(session: Session, token: str, settings: Settings) -> User:
    try:
        payload = decode_token(token, TokenType.ACCESS, settings)
    except InvalidTokenError as exc:
        raise AuthenticationError("Credenciais inválidas ou expiradas.") from exc

    user = session.get(User, payload.user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Credenciais inválidas ou expiradas.")
    return user


def _issue_tokens(session: Session, user_id: uuid.UUID, settings: Settings) -> TokenResponse:
    access_token, _ = create_token(user_id, TokenType.ACCESS, settings)
    refresh_token, refresh_payload = create_token(user_id, TokenType.REFRESH, settings)
    session.add(
        RefreshToken(
            id=refresh_payload.token_id, user_id=user_id, expires_at=refresh_payload.expires_at
        )
    )
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.access_token_expire_minutes * 60,
    )


def _revoke_all_refresh_tokens(session: Session, user_id: uuid.UUID) -> None:
    session.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
