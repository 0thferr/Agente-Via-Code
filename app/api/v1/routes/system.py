import logging

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import SessionDep, get_redis_client

router = APIRouter(tags=["system"])
logger = logging.getLogger(__name__)


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness: o processo está de pé. Não verifica dependências."""
    return {"status": "ok"}


@router.get("/ready")
def ready(session: SessionDep, redis_client: Redis = Depends(get_redis_client)) -> JSONResponse:
    """Readiness: banco e Redis respondem. Usado por orquestradores antes de rotear tráfego."""
    checks: dict[str, str] = {}
    try:
        session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except SQLAlchemyError:
        logger.exception("readiness_database_failed")
        checks["database"] = "unavailable"
    try:
        redis_client.ping()
        checks["redis"] = "ok"
    except RedisError:
        logger.exception("readiness_redis_failed")
        checks["redis"] = "unavailable"

    is_ready = all(value == "ok" for value in checks.values())
    return JSONResponse(
        status_code=status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"status": "ready" if is_ready else "not_ready", "checks": checks},
    )
