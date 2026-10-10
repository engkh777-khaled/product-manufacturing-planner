"""Application settings, read from environment variables and `.env` (never committed).

See `.env.example` for every variable. Secrets use SecretStr so they never appear in logs or reprs.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, model_validator
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
    demo_user_password: SecretStr | None = None  # dev only: shared password of the fictional demo users

    # supabase = real sign-in (M05). disabled = fictional demo planner, allowed only in development/test.
    auth_mode: Literal["supabase", "disabled"] = "supabase"

    health_timeout_seconds: float = 5.0

    # Finite-capacity scheduler (M09): CP-SAT runs in a worker process so a solve never blocks web requests.
    scheduler_worker: Literal["process", "thread"] = "process"
    scheduler_time_limit_seconds: float = 10.0

    @model_validator(mode="after")
    def _auth_mode_is_safe(self) -> "Settings":
        if self.auth_mode == "disabled" and self.app_env not in ("development", "test"):
            raise ValueError("AUTH_MODE=disabled is only allowed when APP_ENV is development or test")
        if self.data_backend == "supabase" and self.auth_mode != "supabase":
            raise ValueError("DATA_BACKEND=supabase reads data as the signed-in user: it requires AUTH_MODE=supabase")
        return self

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_publishable_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
