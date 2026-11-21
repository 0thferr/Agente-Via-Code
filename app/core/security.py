"""Hash de senhas e emissão/validação de tokens JWT."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import Settings

_password_hasher = PasswordHasher()


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


class InvalidTokenError(Exception):
    """Token ausente, expirado, adulterado ou de tipo incorreto."""


@dataclass(frozen=True)
class TokenPayload:
    user_id: uuid.UUID
    token_id: uuid.UUID
    token_type: TokenType
    expires_at: datetime


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def create_token(
    user_id: uuid.UUID, token_type: TokenType, settings: Settings
) -> tuple[str, TokenPayload]:
    now = datetime.now(UTC)
    if token_type is TokenType.ACCESS:
        expires_at = now + timedelta(minutes=settings.access_token_expire_minutes)
    else:
        expires_at = now + timedelta(days=settings.refresh_token_expire_days)

    payload = TokenPayload(
        user_id=user_id, token_id=uuid.uuid4(), token_type=token_type, expires_at=expires_at
    )
    claims = {
        "sub": str(user_id),
        "jti": str(payload.token_id),
        "type": token_type.value,
        "iat": now,
        "exp": expires_at,
    }
    token = jwt.encode(claims, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, payload


def decode_token(token: str, expected_type: TokenType, settings: Settings) -> TokenPayload:
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "jti", "type", "exp"]},
        )
        token_type = TokenType(claims["type"])
        user_id = uuid.UUID(claims["sub"])
        token_id = uuid.UUID(claims["jti"])
    except (jwt.PyJWTError, ValueError) as exc:
        raise InvalidTokenError("Token inválido.") from exc

    if token_type is not expected_type:
        raise InvalidTokenError("Tipo de token incorreto.")

    return TokenPayload(
        user_id=user_id,
        token_id=token_id,
        token_type=token_type,
        expires_at=datetime.fromtimestamp(claims["exp"], tz=UTC),
    )
