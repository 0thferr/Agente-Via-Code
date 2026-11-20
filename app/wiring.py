"""Montagem dos componentes configuráveis (IA, provedores, enviadores).

Fica fora de `app/api` para que a API e o worker usem exatamente a mesma
configuração, sem o worker depender da camada HTTP.
"""

from functools import lru_cache

from pydantic import SecretStr

from app.ai.llm import StructuredLLM
from app.ai.openai_client import OpenAIStructuredLLM
from app.core.config import AIProviderName, Settings, get_settings
from app.core.crypto import SecretCipher
from app.integrations.application_submitters.mock import MockApplicationSubmitter
from app.integrations.application_submitters.registry import SubmitterRegistry
from app.integrations.email.base import EmailClient
from app.integrations.email.oauth_clients import GmailClient, OutlookClient
from app.integrations.email.simulated import SimulatedEmailClient
from app.integrations.job_providers.registry import ProviderRegistry, build_default_registry
from app.integrations.notifications.channels import (
    ChannelRegistry,
    SmtpEmailChannel,
    TelegramChannel,
    WebhookChannel,
)
from app.models.enums import EmailProvider
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


def _secret(value: SecretStr | None) -> str | None:
    """Valor de um SecretStr opcional; string vazia conta como ausente."""
    if value is None:
        return None
    return value.get_secret_value() or None


@lru_cache
def get_cipher() -> SecretCipher:
    return SecretCipher(_secret(get_settings().token_encryption_key))


@lru_cache
def get_email_clients() -> dict[EmailProvider, EmailClient]:
    """Conectores de e-mail disponíveis: só os que têm credenciais OAuth configuradas."""
    settings: Settings = get_settings()
    clients: dict[EmailProvider, EmailClient] = {}
    google_secret = _secret(settings.google_client_secret)
    if settings.google_client_id and google_secret:
        clients[EmailProvider.GMAIL] = GmailClient(
            client_id=settings.google_client_id, client_secret=google_secret
        )
    microsoft_secret = _secret(settings.microsoft_client_secret)
    if settings.microsoft_client_id and microsoft_secret:
        clients[EmailProvider.OUTLOOK] = OutlookClient(
            client_id=settings.microsoft_client_id, client_secret=microsoft_secret
        )
    if settings.enable_simulated_providers:
        clients[EmailProvider.SIMULATED] = SimulatedEmailClient()
    return clients


@lru_cache
def get_channel_registry() -> ChannelRegistry:
    settings = get_settings()
    return ChannelRegistry(
        [
            SmtpEmailChannel(
                host=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_username,
                password=_secret(settings.smtp_password),
                sender=settings.smtp_from,
                timeout_seconds=settings.notification_timeout_seconds,
            ),
            TelegramChannel(
                bot_token=_secret(settings.telegram_bot_token),
                timeout_seconds=settings.notification_timeout_seconds,
            ),
            WebhookChannel(timeout_seconds=settings.notification_timeout_seconds),
        ]
    )
