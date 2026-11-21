import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

from app.core.timezones import validate_timezone


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=2048)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 (tipo do token, não um segredo)
    expires_in: int = Field(description="Validade do access token, em segundos.")


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    is_active: bool
    timezone: str
    created_at: datetime


class UserUpdate(BaseModel):
    timezone: Annotated[str, AfterValidator(validate_timezone)] = Field(
        description="Fuso IANA (ex.: America/Sao_Paulo)."
    )
