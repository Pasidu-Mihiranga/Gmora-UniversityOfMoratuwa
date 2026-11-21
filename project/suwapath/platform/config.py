"""Settings from the repository-root `.env` (ARC-15). No module reads os.environ directly."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    suwapath_env: Literal["development", "demo", "production", "test"] = "development"
    database_url: str = "postgresql+psycopg://suwapath:suwapath@localhost:5432/suwapath"
    web_origins: str = "http://localhost:5173"

    model_provider: Literal["", "fake", "gemini", "openai_compatible"] = ""
    model_name: str = ""
    model_name_fast: str = ""
    google_genai_use_vertexai: bool = True
    google_cloud_project: str = ""
    google_cloud_location: str = "us-central1"
    google_application_credentials: str = ""
    google_api_key: SecretStr = SecretStr("")
    openai_compat_preset: Literal["", "openai", "openrouter", "groq", "ollama"] = ""
    openai_compat_api_key: SecretStr = SecretStr("")

    decision_provider: Literal["", "decisions", "none"] = ""
    openrouter_api_key: SecretStr = SecretStr("")
    jev_model: str = "typesafe/jev-1.14"

    jwt_secret: SecretStr = SecretStr("")
    suwapath_master_key: SecretStr = SecretStr("")
    demo_password: SecretStr = SecretStr("")

    access_token_minutes: int = Field(default=15, ge=1, le=60)
    refresh_token_days: int = Field(default=7, ge=1, le=30)

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.web_origins.split(",") if o.strip()]

    @property
    def dev_tools_enabled(self) -> bool:
        """Simulated clock, scenario reset and demo accounts (EVT-17)."""
        return self.suwapath_env in ("development", "demo", "test")

    @property
    def resolved_model_provider(self) -> Literal["fake", "gemini", "openai_compatible"]:
        """GW-3: explicit choice, else Gemini when Google credentials exist, else fake."""
        if self.model_provider:
            return self.model_provider
        has_google = bool(
            self.google_application_credentials or self.google_api_key.get_secret_value()
        )
        return "gemini" if has_google else "fake"

    @property
    def resolved_decision_provider(self) -> Literal["decisions", "none"]:
        if self.decision_provider:
            return self.decision_provider
        return "decisions" if self.openrouter_api_key.get_secret_value() else "none"

    @model_validator(mode="after")
    def _production_requires_secrets(self) -> Settings:
        if self.suwapath_env == "production":
            missing = [
                name
                for name, value in (
                    ("JWT_SECRET", self.jwt_secret),
                    ("SUWAPATH_MASTER_KEY", self.suwapath_master_key),
                )
                if not value.get_secret_value()
            ]
            if missing:
                raise ValueError(f"production requires {', '.join(missing)} (SEC-2, SEC-21)")
            if self.demo_password.get_secret_value():
                raise ValueError("DEMO_PASSWORD must be empty in production (16 section 2)")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
