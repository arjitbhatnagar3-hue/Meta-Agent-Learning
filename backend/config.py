"""Validated application settings loaded from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration shared by the API, worker, and agent runtime."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_mode: Literal["fake", "huggingface"] = "fake"
    hf_token: SecretStr = SecretStr("")
    hf_api_base_url: str = "https://router.huggingface.co/v1"
    hf_meta_model_id: str = "Qwen/Qwen3.8-2.4T-A95B"
    hf_worker_model_id: str = "Qwen/Qwen3.8-2.4T-A95B"
    hf_report_model_id: str = "Qwen/Qwen3.8-2.4T-A95B"
    hf_temperature: float = Field(default=0, ge=0, le=2)
    hf_max_tokens: int = Field(default=1800, ge=64, le=32768)
    hf_timeout_seconds: float = Field(default=120, gt=0)
    hf_max_retries: int = Field(default=3, ge=1, le=10)
    hf_max_concurrent_requests: int = Field(default=1, ge=1, le=20)

    # Use Supabase's direct or session-pooler Postgres URL here for durable
    # task/history storage. SQLite remains the convenient local fallback.
    database_url: str = "sqlite+aiosqlite:///./meta_agentx_local.db"

    # Optional Supabase Storage. The service-role key is server-only and must
    # never be exposed to frontend JavaScript.
    supabase_url: str = ""
    supabase_service_role_key: SecretStr = SecretStr("")
    supabase_storage_bucket: str = "meta-agentx-documents"

    # Read-only, server-side API credentials for the first supported connectors.
    hubspot_access_token: SecretStr = SecretStr("")
    stripe_secret_key: SecretStr = SecretStr("")
    zendesk_subdomain: str = ""
    zendesk_email: str = ""
    zendesk_api_token: SecretStr = SecretStr("")

    # Synthetic enterprise-data adapters are intentionally not registered;
    # provider tools are real, read-only, and gated on server-side credentials.

    worker_poll_seconds: float = Field(default=1, gt=0)
    worker_heartbeat_seconds: int = Field(default=10, ge=1, le=300)
    worker_lease_seconds: int = Field(default=300, ge=30)
    max_task_attempts: int = Field(default=3, ge=1, le=10)
    max_agent_iterations: int = Field(default=6, ge=1, le=20)
    agent_retry_attempts: int = Field(default=2, ge=0, le=5)
    agent_timeout_seconds: float = Field(default=120, gt=0)
    conversation_memory_runs: int = Field(default=3, ge=0, le=10)
    log_level: str = "INFO"

    @property
    def supabase_storage_configured(self) -> bool:
        return bool(
            self.supabase_url.strip()
            and self.supabase_service_role_key.get_secret_value().strip()
        )

    @property
    def supabase_storage_partially_configured(self) -> bool:
        return bool(self.supabase_url.strip()) != bool(
            self.supabase_service_role_key.get_secret_value().strip()
        )


@lru_cache
def get_settings() -> Settings:
    """Return one settings instance per process."""
    return Settings()