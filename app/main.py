import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging

logger = logging.getLogger("app.request")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
FRONTEND_PREFIX = "/app"
# Só scripts e estilos do próprio servidor; nada embutido, nada de terceiros. Mesmo que
# um texto externo (descrição de vaga, e-mail) contenha HTML/JS, o navegador não executa.
FRONTEND_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def _mount_frontend(app: FastAPI) -> None:
    """Serve o dashboard web (arquivos estáticos) em /app, na mesma origem da API."""
    if not FRONTEND_DIR.is_dir():
        logger.warning("frontend_not_found", extra={"path": str(FRONTEND_DIR)})
        return
    app.mount(FRONTEND_PREFIX, StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse(f"{FRONTEND_PREFIX}/")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        version="1.6.0",
        description="API do agente pessoal de candidaturas JobPilot AI.",
    )
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )

    @app.middleware("http")
    async def log_requests(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # Erro não tratado: registra com o request_id para rastrear, e repassa ao
            # FastAPI, que responde 500 sem expor detalhes ao cliente.
            logger.exception(
                "request_failed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                },
            )
            raise
        response.headers["X-Request-ID"] = request_id
        if request.url.path.startswith(FRONTEND_PREFIX):
            response.headers.update(FRONTEND_SECURITY_HEADERS)
        # Registramos só método, rota e status: nunca corpo, query string ou cabeçalhos,
        # que podem conter dados pessoais ou tokens.
        logger.info(
            "request_completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        return response

    register_exception_handlers(app)
    app.include_router(api_router)
    _mount_frontend(app)
    return app


app = create_app()
