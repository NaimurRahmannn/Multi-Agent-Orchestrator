from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigurationError(RuntimeError):
    """Raised when an operation-specific configuration requirement is missing."""


class Settings(BaseSettings):
    """Application settings loaded from environment variables and `.env`."""

    groq_api_key: SecretStr | None = Field(default=None, alias="GROQ_API_KEY")
    groq_model: str | None = Field(default=None, alias="GROQ_MODEL")
    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        alias="LOG_LEVEL",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    def require_groq(self) -> None:
        """Validate settings needed before making a live Groq request."""
        missing: list[str] = []
        if not self.groq_api_key or not self.groq_api_key.get_secret_value().strip():
            missing.append("GROQ_API_KEY")
        if not self.groq_model or not self.groq_model.strip():
            missing.append("GROQ_MODEL")
        if missing:
            names = ", ".join(missing)
            raise ConfigurationError(
                f"Missing required Groq configuration: {names}. "
                "Set these values in .env or the process environment before running live checks."
            )

    @property
    def groq_api_key_value(self) -> str:
        self.require_groq()
        assert self.groq_api_key is not None
        return self.groq_api_key.get_secret_value()

    @property
    def groq_model_value(self) -> str:
        self.require_groq()
        assert self.groq_model is not None
        return self.groq_model


@lru_cache
def get_settings() -> Settings:
    return Settings()
