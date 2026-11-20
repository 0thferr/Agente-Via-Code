"""Dependências compartilhadas pelas rotas (injeção de dependências do FastAPI).

Centralizar aqui facilita substituir implementações nos testes via
`app.dependency_overrides`.
"""

from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis import Redis
from sqlalchemy.orm import Session

from app.ai.llm import StructuredLLM
from app.ai.openai_client import OpenAIStructuredLLM
from app.core.config import AIProviderName, Settings, get_settings
from app.core.errors import AuthenticationError
from app.db.session import get_db_session
from app.integrations.application_submitters.mock import MockApplicationSubmitter
from app.integrations.application_submitters.registry import SubmitterRegistry
from app.integrations.job_providers.registry import ProviderRegistry, build_default_registry
from app.models.user import User
from app.services import auth_service
from app.services.analysis.requirements import (
    KeywordRequirementAnalyzer,
    LLMRequirementAnalyzer,
    RequirementAnalyzer,
)
from app.services.cover_letters.writers import (
    CoverLetterWriter,
    LLMCoverLetterWriter,
    TemplateCoverLetterWriter,
)
from app.services.file_storage import LocalFileStorage
from app.services.rate_limiter import RateLimiter, RedisRateLimiter

_bearer_scheme = HTTPBearer(auto_error=False)

SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[Session, Depends(get_db_session)]


@lru_cache
def get_redis_client() -> Redis:
    return Redis.from_url(get_settings().redis_url, socket_timeout=2, socket_connect_timeout=2)


def get_rate_limiter() -> RateLimiter:
    return RedisRateLimiter(get_redis_client())


@lru_cache
def get_provider_registry() -> ProviderRegistry:
    return build_default_registry(include_simulated=get_settings().enable_simulated_providers)


@lru_cache
def get_llm() -> StructuredLLM | None:
    """Cliente do modelo de linguagem configurado, ou None no modo simulado."""
    settings = get_settings()
    if settings.ai_provider is AIProviderName.SIMULATED or settings.openai_api_key is None:
        return None
    return OpenAIStructuredLLM(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        base_url=settings.openai_base_url,
        timeout_seconds=settings.ai_timeout_seconds,
    )


def get_requirement_analyzer() -> RequirementAnalyzer:
    llm = get_llm()
    return LLMRequirementAnalyzer(llm) if llm else KeywordRequirementAnalyzer()


def get_cover_letter_writer() -> CoverLetterWriter:
    llm = get_llm()
    return LLMCoverLetterWriter(llm) if llm else TemplateCoverLetterWriter()


@lru_cache
def get_submitter_registry() -> SubmitterRegistry:
    """Enviadores disponíveis. Hoje só o simulado: nenhuma plataforma real tem
    integração de candidatura autorizada configurada."""
    simulated = [MockApplicationSubmitter()] if get_settings().enable_simulated_providers else []
    return SubmitterRegistry(simulated)


def get_file_storage(settings: SettingsDep) -> LocalFileStorage:
    return LocalFileStorage(settings.storage_dir)


def get_current_user(
    session: SessionDep,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> User:
    if credentials is None:
        raise AuthenticationError("Autenticação necessária.")
    return auth_service.get_user_from_access_token(session, credentials.credentials, settings)


def client_identifier(request: Request) -> str:
    # Atrás de um proxy reverso, configure o uvicorn com --proxy-headers para que
    # request.client reflita o IP real do cliente.
    return request.client.host if request.client else "unknown"


CurrentUserDep = Annotated[User, Depends(get_current_user)]
RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]
FileStorageDep = Annotated[LocalFileStorage, Depends(get_file_storage)]
RequirementAnalyzerDep = Annotated[RequirementAnalyzer, Depends(get_requirement_analyzer)]
CoverLetterWriterDep = Annotated[CoverLetterWriter, Depends(get_cover_letter_writer)]
SubmitterRegistryDep = Annotated[SubmitterRegistry, Depends(get_submitter_registry)]
ProviderRegistryDep = Annotated[ProviderRegistry, Depends(get_provider_registry)]
