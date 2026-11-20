from datetime import UTC, datetime

from fastapi import APIRouter, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.api.deps import (
    CurrentUserDep,
    FileStorageDep,
    RateLimiterDep,
    SessionDep,
    SettingsDep,
)
from app.schemas.privacy import AccountDeletionRequest
from app.services import privacy_service

router = APIRouter(prefix="/privacy", tags=["privacy"])

EXPORT_RATE_LIMIT = 5
EXPORT_RATE_WINDOW_SECONDS = 3600


@router.get("/export")
def export_data(
    session: SessionDep,
    current_user: CurrentUserDep,
    rate_limiter: RateLimiterDep,
) -> JSONResponse:
    """Exporta todos os seus dados em JSON (LGPD: acesso e portabilidade)."""
    rate_limiter.hit(
        f"privacy:export:{current_user.id}", EXPORT_RATE_LIMIT, EXPORT_RATE_WINDOW_SECONDS
    )
    data = privacy_service.export_user_data(session, current_user)
    filename = f"jobpilot-dados-{datetime.now(UTC):%Y%m%d}.json"
    return JSONResponse(
        content=jsonable_encoder(data),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/account-deletion", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    payload: AccountDeletionRequest,
    session: SessionDep,
    settings: SettingsDep,
    storage: FileStorageDep,
    current_user: CurrentUserDep,
    rate_limiter: RateLimiterDep,
) -> Response:
    """Exclui a conta e todos os dados dela, de forma irreversível (LGPD: eliminação).

    Exige a senha atual e a frase de confirmação. É um POST (e não DELETE com corpo)
    porque alguns clientes e proxies descartam o corpo de requisições DELETE.
    """
    rate_limiter.hit(
        f"privacy:delete:{current_user.id}",
        settings.login_rate_limit_attempts,
        settings.login_rate_limit_window_seconds,
    )
    privacy_service.delete_account(session, storage, current_user, payload.password)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
