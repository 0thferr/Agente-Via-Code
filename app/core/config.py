from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_SECRET_KEY_LENGTH = 32


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class AIProviderName(StrEnum):
    SIMULATED = "simulated"
    OPENAI = "openai"


class Settings(BaseSettings):
    """Configuração carregada de variáveis de ambiente (ou de um arquivo .env)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "JobPilot AI"
    app_env: Environment = Environment.DEVELOPMENT
    log_level: str = "INFO"

    database_url: str = "postgresql+psycopg://jobpilot:jobpilot@localhost:5432/jobpilot"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret_key: str = Field(min_length=MIN_SECRET_KEY_LENGTH)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(default=15, gt=0)
    refresh_token_expire_days: int = Field(default=7, gt=0)

    login_rate_limit_attempts: int = Field(default=5, gt=0)
    login_rate_limit_window_seconds: int = Field(default=60, gt=0)

    storage_dir: Path = Path("./storage")
    max_resume_size_mb: int = Field(default=5, gt=0)

    # Provedor com vagas fictícias, útil em desenvolvimento e demonstrações.
    enable_simulated_providers: bool = True

    # "simulated" funciona sem chave e sem custo (regras por palavras-chave);
    # "openai" usa a API da OpenAI e exige OPENAI_API_KEY.
    ai_provider: AIProviderName = AIProviderName.SIMULATED
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4o-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    ai_timeout_seconds: float = Field(default=60, gt=0)

    # Worker de automações (processo separado da API).
    worker_tick_seconds: int = Field(default=30, ge=5)
    worker_lease_seconds: int = Field(default=900, ge=60)
    worker_max_jobs_per_tick: int = Field(default=5, ge=1, le=50)
    stale_submission_minutes: int = Field(default=15, ge=5)

    # Chave Fernet para cifrar tokens OAuth e segredos de webhook. Gere com:
    # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    token_encryption_key: SecretStr | None = None

    # OAuth de e-mail (registre um app no Google Cloud / Microsoft Entra).
    oauth_redirect_base_url: str = "http://localhost:8000"
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    microsoft_client_id: str | None = None
    microsoft_client_secret: SecretStr | None = None
    email_sync_lookback_days: int = Field(default=14, ge=1, le=90)

    # Canais de notificação (opcionais).
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str | None = None
    telegram_bot_token: SecretStr | None = None
    notification_timeout_seconds: float = Field(default=10, gt=0)

    cors_origins: list[str] = Field(default_factory=list)

    @field_validator("jwt_secret_key")
    @classmethod
    def reject_example_secret(cls, value: str) -> str:
        if value.startswith("change-me"):
            raise ValueError("JWT_SECRET_KEY ainda usa o valor de exemplo; gere um segredo real.")
        return value

    @model_validator(mode="after")
    def require_openai_key(self) -> "Settings":
        has_key = self.openai_api_key is not None and self.openai_api_key.get_secret_value()
        if self.ai_provider is AIProviderName.OPENAI and not has_key:
            raise ValueError("AI_PROVIDER=openai exige a variável OPENAI_API_KEY.")
        return self

    @property
    def max_resume_size_bytes(self) -> int:
        return self.max_resume_size_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
