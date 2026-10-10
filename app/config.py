"""Application settings, read from environment variables and `.env` (never committed).

See `.env.example` for every variable. Secrets use SecretStr so they never appear in logs or reprs.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "test", "staging", "production"] = "development"

    # Which repository implementation serves data: in-memory fictional data, or Supabase (from M06).
    data_backend: Literal["mock", "supabase"] = "mock"

    # Supabase project (cloud dev project for now — see development-khaled/decisions.md D-014).
    supabase_url: str | None = None
    supabase_publishable_key: str | None = None  # safe for browsers; RLS applies
    supabase_secret_key: SecretStr | None = None  # server-only; bypasses RLS — never send to templates
    database_url: SecretStr | None = None  # direct Postgres connection for planning snapshots (M08)

    health_timeout_seconds: float = 5.0

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_publishable_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
