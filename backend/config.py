"""Validated application settings loaded from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration shared by the API, worker, and agent runtime."""

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    # Use "fake" for free development.
    # Use "huggingface" for real paid inference.
    llm_mode: Literal["fake", "huggingface"] = "fake"

    hf_token: SecretStr = SecretStr("")
    hf_api_base_url: str = "https://router.huggingface.co/v1"

    hf_meta_model_id: str = "Qwen/Qwen3.8-2.4T-A95B"
    hf_worker_model_id: str = "Qwen/Qwen3.8-2.4T-A95B"
    hf_report_model_id: str = "Qwen/Qwen3.8-2.4T-A95B"

    hf_temperature: float = Field(
        default=0,
        ge=0,
        le=2,
    )

    hf_max_tokens: int = Field(
        default=1800,
        ge=64,
        le=32768,
    )

    hf_timeout_seconds: float = Field(
        default=120,
        gt=0,
    )

    hf_max_retries: int = Field(
        default=3,
        ge=1,
        le=10,
    )

    hf_max_concurrent_requests: int = Field(
        default=1,
        ge=1,
        le=20,
    )

    database_url: str = (
        "sqlite+aiosqlite:///./meta_agentx.db"
    )

    worker_poll_seconds: float = Field(
        default=1,
        gt=0,
    )

    worker_lease_seconds: int = Field(
        default=300,
        ge=30,
    )

    max_task_attempts: int = Field(
        default=3,
        ge=1,
        le=10,
    )

    max_agent_iterations: int = Field(
        default=6,
        ge=1,
        le=20,
    )

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Return one settings instance per process."""

    return Settings()