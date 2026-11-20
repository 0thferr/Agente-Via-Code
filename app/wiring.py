"""Montagem dos componentes configuráveis (IA, provedores, enviadores).

Fica fora de `app/api` para que a API e o worker usem exatamente a mesma
configuração, sem o worker depender da camada HTTP.
"""

from functools import lru_cache

from app.ai.llm import StructuredLLM
from app.ai.openai_client import OpenAIStructuredLLM
from app.core.config import AIProviderName, get_settings
from app.integrations.application_submitters.mock import MockApplicationSubmitter
from app.integrations.application_submitters.registry import SubmitterRegistry
from app.integrations.job_providers.registry import ProviderRegistry, build_default_registry
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
