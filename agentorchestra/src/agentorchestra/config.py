import os
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from agentorchestra.exceptions import ConfigurationError

DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class GroqConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    api_key: str
    model: str


class GroqAgentName(str, Enum):  # noqa: UP042 - StrEnum requires Python 3.11.
    """Live agents with independently configured Groq credentials."""

    MANAGER = "manager"
    HTML = "html"
    CSS = "css"


class Settings(BaseSettings):
    """Application settings loaded from environment variables and `.env`."""

    groq_manager_api_key: SecretStr | None = Field(default=None, alias="GROQ_MANAGER_API_KEY")
    groq_html_api_key: SecretStr | None = Field(default=None, alias="GROQ_HTML_API_KEY")
    groq_css_api_key: SecretStr | None = Field(default=None, alias="GROQ_CSS_API_KEY")
    groq_model: str | None = Field(default=None, alias="GROQ_MODEL")
    app_env: str = Field(default="development", alias="APP_ENV")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        alias="LOG_LEVEL",
    )
    project_root: Path = Field(default=DEFAULT_PROJECT_ROOT, alias="AGENTORCHESTRA_ROOT")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator("project_root", mode="before")
    @classmethod
    def resolve_project_root(cls, value: object) -> Path:
        if value is None or value == "":
            return DEFAULT_PROJECT_ROOT.resolve()
        return Path(value).expanduser().resolve()

    @field_validator("groq_model", "app_env", mode="before")
    @classmethod
    def strip_optional_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @property
    def source_dir(self) -> Path:
        return self._derive_path("src")

    @property
    def fixture_site_dir(self) -> Path:
        return self._derive_path("sites", "fixture")

    @property
    def working_site_dir(self) -> Path:
        return self._derive_path("sites", "working")

    @property
    def staging_root_dir(self) -> Path:
        return self._derive_path("sites", "staging")

    @property
    def reports_root_dir(self) -> Path:
        return self._derive_path("reports")

    @property
    def lighthouse_report_dir(self) -> Path:
        return self._derive_path("reports", "lighthouse")

    @property
    def screenshot_report_dir(self) -> Path:
        return self._derive_path("reports", "screenshots")

    @property
    def routing_report_dir(self) -> Path:
        return self._derive_path("reports", "routing")

    def _derive_path(self, *parts: str) -> Path:
        # Preserve the final path's symlink identity for filesystem safety checks.
        path = Path(os.path.abspath(self.project_root / Path(*parts)))
        _ensure_inside_root(self.project_root, path)
        return path

    def require_groq_configuration(self, agent: GroqAgentName) -> GroqConfiguration:
        """Return the selected agent's Groq key and the shared model."""
        key_fields = {
            GroqAgentName.MANAGER: (self.groq_manager_api_key, "GROQ_MANAGER_API_KEY"),
            GroqAgentName.HTML: (self.groq_html_api_key, "GROQ_HTML_API_KEY"),
            GroqAgentName.CSS: (self.groq_css_api_key, "GROQ_CSS_API_KEY"),
        }
        api_key, api_key_name = key_fields[agent]
        missing: list[str] = []
        if not api_key or not api_key.get_secret_value().strip():
            missing.append(api_key_name)
        if not self.groq_model or not self.groq_model.strip():
            missing.append("GROQ_MODEL")
        if missing:
            names = ", ".join(missing)
            raise ConfigurationError(
                f"Missing required Groq configuration for {agent.value} agent: {names}. "
                "Set these values in .env or the process environment before running live checks."
            )
        return GroqConfiguration(
            api_key=api_key.get_secret_value(),
            model=self.groq_model.strip(),
        )

    def require_groq(self) -> None:
        """Validate the Manager credentials used by the basic Groq feasibility check."""
        self.require_groq_configuration(GroqAgentName.MANAGER)

    @property
    def groq_api_key_value(self) -> str:
        """Return the Manager key used by the basic Groq feasibility check."""
        return self.require_groq_configuration(GroqAgentName.MANAGER).api_key

    @property
    def groq_api_key_values(self) -> tuple[str, ...]:
        """Return configured keys for output redaction without exposing missing values."""
        keys = (
            self.groq_manager_api_key,
            self.groq_html_api_key,
            self.groq_css_api_key,
        )
        return tuple(
            secret.get_secret_value()
            for secret in keys
            if secret is not None and secret.get_secret_value()
        )

    @property
    def groq_model_value(self) -> str:
        return self.require_groq_configuration(GroqAgentName.MANAGER).model


def _ensure_inside_root(project_root: Path, path: Path) -> None:
    try:
        path.relative_to(project_root.resolve())
    except ValueError as exc:
        raise ConfigurationError(f"Configured path escapes project root: {path}") from exc


def ensure_runtime_directories(settings: Settings) -> None:
    """Create only runtime directories that are safe to regenerate."""
    runtime_dirs = (
        settings.staging_root_dir,
        settings.lighthouse_report_dir,
        settings.screenshot_report_dir,
        settings.routing_report_dir,
    )
    for directory in runtime_dirs:
        _ensure_inside_root(settings.project_root, directory)
        directory.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
