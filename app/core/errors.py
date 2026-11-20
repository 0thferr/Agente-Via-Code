"""Exceções de domínio e seu mapeamento para respostas HTTP.

Os serviços lançam exceções de domínio (sem conhecer HTTP); a camada de API
traduz cada uma para o status adequado em um único lugar.
"""

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse


class DomainError(Exception):
    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "domain_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(DomainError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class AuthenticationError(DomainError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "authentication_failed"


class ConflictError(DomainError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class ValidationError(DomainError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "validation_error"


class RateLimitExceededError(DomainError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limit_exceeded"


class IntegrationError(DomainError):
    status_code = status.HTTP_502_BAD_GATEWAY
    code = "integration_error"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def handle_domain_error(_: Request, exc: DomainError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if isinstance(exc, AuthenticationError) else None
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
            headers=headers,
        )
