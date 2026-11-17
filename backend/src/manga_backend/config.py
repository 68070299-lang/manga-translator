"""Backend runtime configuration.

Every environment variable the HTTP layer reads is declared here. Values come
from the process environment first, then from backend/.env (skipped when
MANGA_TRANSLATOR_TESTING=1, the hermetic test mode).

Invalid numeric limits fall back to their defaults with a warning instead of
failing startup, matching the behavior the extension has always relied on.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from manga_backend.bootstrap import is_testing_environment
from manga_backend.paths import ENV_FILE

logger = logging.getLogger(__name__)

DEFAULT_MAX_CONCURRENT = 2
DEFAULT_QUEUE_TIMEOUT_SECONDS = 600.0
DEFAULT_LOCAL_LLM_BASE_URL = "http://localhost:8000/v1"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8001

# Environment variable that supplies each cloud translator's server-side API key.
TRANSLATOR_API_KEY_ENV_VARS = {
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}


def _positive_or_default(name: str, value: Any, default: Any, cast: type[int] | type[float]) -> Any:
    """Return a positive number, or the default (with a warning) when unset or invalid."""
    raw = str(value).strip()
    if not raw:
        return default
    try:
        parsed = cast(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; using %s", name, raw, default)
        return default
    if math.isnan(parsed) or not parsed > 0:
        logger.warning("Invalid %s=%r; using %s", name, raw, default)
        return default
    return parsed


class Settings(BaseSettings):
    """Process configuration, read once when the app is created."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE, extra="ignore", case_sensitive=False, frozen=True
    )

    auth_token: str = Field(
        default="", validation_alias=AliasChoices("BACKEND_AUTH_TOKEN", "auth_token")
    )
    allowed_origins: Annotated[frozenset[str], NoDecode] = Field(
        default=frozenset(),
        validation_alias=AliasChoices("BACKEND_ALLOWED_ORIGINS", "allowed_origins"),
    )
    max_concurrent: int = Field(
        default=DEFAULT_MAX_CONCURRENT,
        validation_alias=AliasChoices("BACKEND_MAX_CONCURRENT", "max_concurrent"),
    )
    queue_timeout_seconds: float = Field(
        default=DEFAULT_QUEUE_TIMEOUT_SECONDS,
        validation_alias=AliasChoices("BACKEND_QUEUE_TIMEOUT_SECONDS", "queue_timeout_seconds"),
    )
    testing: bool = Field(
        default=False, validation_alias=AliasChoices("MANGA_TRANSLATOR_TESTING", "testing")
    )
    use_gpu: bool = Field(default=False, validation_alias=AliasChoices("USE_GPU", "use_gpu"))
    host: str = Field(default=DEFAULT_HOST, validation_alias=AliasChoices("BACKEND_HOST", "host"))
    port: int = Field(default=DEFAULT_PORT, validation_alias=AliasChoices("BACKEND_PORT", "port"))
    local_llm_base_url: str = Field(
        default=DEFAULT_LOCAL_LLM_BASE_URL,
        validation_alias=AliasChoices("LOCAL_LLM_BASE_URL", "local_llm_base_url"),
    )
    gemini_api_key: str = Field(
        default="", validation_alias=AliasChoices("GEMINI_API_KEY", "gemini_api_key")
    )
    openai_api_key: str = Field(
        default="", validation_alias=AliasChoices("OPENAI_API_KEY", "openai_api_key")
    )
    openrouter_api_key: str = Field(
        default="", validation_alias=AliasChoices("OPENROUTER_API_KEY", "openrouter_api_key")
    )

    @field_validator("auth_token", mode="before")
    @classmethod
    def _strip_token(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: Any) -> Any:
        if isinstance(value, str):
            return frozenset(origin.strip() for origin in value.split(",") if origin.strip())
        return value

    @field_validator("max_concurrent", mode="before")
    @classmethod
    def _positive_concurrency(cls, value: Any) -> Any:
        return _positive_or_default("BACKEND_MAX_CONCURRENT", value, DEFAULT_MAX_CONCURRENT, int)

    @field_validator("queue_timeout_seconds", mode="before")
    @classmethod
    def _positive_timeout(cls, value: Any) -> Any:
        return _positive_or_default(
            "BACKEND_QUEUE_TIMEOUT_SECONDS", value, DEFAULT_QUEUE_TIMEOUT_SECONDS, float
        )

    @field_validator("testing", mode="before")
    @classmethod
    def _testing_flag(cls, value: Any) -> Any:
        # Only the exact string "1" enables test mode, as in the process-level check.
        return value == "1" if isinstance(value, str) else value

    @field_validator("use_gpu", mode="before")
    @classmethod
    def _gpu_flag(cls, value: Any) -> Any:
        return value.lower() == "true" if isinstance(value, str) else value

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Hermetic test mode never reads backend/.env. Checked per construction,
        # not at import, so tests can toggle the flag.
        if is_testing_environment():
            dotenv_settings = DotEnvSettingsSource(settings_cls, env_file=None)
        return (init_settings, env_settings, dotenv_settings, file_secret_settings)

    def translator_api_key(self, translator: str) -> str:
        """Server-side API key configured for a cloud translator, or an empty string."""
        match translator:
            case "gemini":
                return self.gemini_api_key.strip()
            case "openai":
                return self.openai_api_key.strip()
            case "openrouter":
                return self.openrouter_api_key.strip()
            case _:
                return ""


@dataclass(frozen=True, slots=True)
class Limits:
    """Fixed request limits; kept apart from Settings because they are not user-tunable."""

    max_upload_size: int = 20 * 1024 * 1024
    max_image_pixels: int = 25_000_000
    # Multipart framing and form fields on top of the raw image bytes.
    upload_overhead_bytes: int = 1024 * 1024
    queue_retry_after_seconds: int = 10


def load_settings() -> Settings:
    """Build Settings from the environment and, outside test mode, backend/.env."""
    return Settings()
