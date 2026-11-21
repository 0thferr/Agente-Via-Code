from fastapi import APIRouter, Request

from app.api.deps import (
    CurrentUserDep,
    RateLimiterDep,
    SessionDep,
    SettingsDep,
    client_identifier,
)
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    TokenResponse,
    UserResponse,
    UserUpdate,
)
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    rate_limiter: RateLimiterDep,
) -> TokenResponse:
    """Autentica com e-mail e senha. Limitado por IP e por e-mail contra força bruta."""
    for key in (f"login:ip:{client_identifier(request)}", f"login:email:{payload.email.lower()}"):
        rate_limiter.hit(
            key, settings.login_rate_limit_attempts, settings.login_rate_limit_window_seconds
        )
    return auth_service.authenticate(session, payload.email, payload.password, settings)


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    payload: RefreshRequest,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    rate_limiter: RateLimiterDep,
) -> TokenResponse:
    """Troca o refresh token por um novo par. O token usado é revogado (rotação)."""
    rate_limiter.hit(
        f"refresh:ip:{client_identifier(request)}",
        settings.login_rate_limit_attempts * 4,
        settings.login_rate_limit_window_seconds,
    )
    return auth_service.refresh_tokens(session, payload.refresh_token, settings)


@router.get("/me", response_model=UserResponse)
def me(current_user: CurrentUserDep) -> UserResponse:
    return UserResponse.model_validate(current_user)


@router.patch("/me", response_model=UserResponse)
def update_me(
    payload: UserUpdate, session: SessionDep, current_user: CurrentUserDep
) -> UserResponse:
    """Atualiza preferências da conta, como o fuso horário."""
    user = auth_service.update_user_settings(session, current_user, payload.timezone)
    return UserResponse.model_validate(user)
