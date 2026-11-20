from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_SECRET_KEY_LENGTH = 32


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


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

    cors_origins: list[str] = Field(default_factory=list)

    @field_validator("jwt_secret_key")
    @classmethod
    def reject_example_secret(cls, value: str) -> str:
        if value.startswith("change-me"):
            raise ValueError("JWT_SECRET_KEY ainda usa o valor de exemplo; gere um segredo real.")
        return value

    @property
    def max_resume_size_bytes(self) -> int:
        return self.max_resume_size_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
