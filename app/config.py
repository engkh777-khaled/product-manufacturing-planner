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

    # Signs validation results sent to record_schedule_run() (M12, D-036). Same value as private.app_config in the DB;
    # set both with `python -m scripts.set_signing_key`. Without it, saved schedules are never marked validated.
    schedule_signing_key: SecretStr | None = None

    # AI planning assistant (M11). Without a key the assistant falls back to scripted demo replies.
    anthropic_api_key: SecretStr | None = None   # server-side only — never sent to templates or browsers
    assistant_model: str = "claude-opus-5-5"
    assistant_max_tool_rounds: int = 8
    assistant_max_tokens: int = 8000             # per model call; thinking counts towards it (cost = tokens used)
    assistant_plan_seconds: float = 8.0          # CP-SAT time limit when a tool needs a fresh plan

    @property
    def assistant_enabled(self) -> bool:
        return bool(self.anthropic_api_key and self.anthropic_api_key.get_secret_value().strip())

    @model_validator(mode="after")
    def _auth_mode_is_safe(self) -> "Settings":
        if self.auth_mode == "disabled" and self.app_env not in ("development", "test"):
            raise ValueError("AUTH_MODE=disabled is only allowed when APP_ENV is development or test")
        if self.data_backend == "supabase" and self.auth_mode != "supabase":
            raise ValueError("DATA_BACKEND=supabase reads data as the signed-in user: it requires AUTH_MODE=supabase")
        if self.app_env == "production":   # M12: refuse to start a misconfigured production instance
            missing = [name for name, ok in (("DATA_BACKEND=supabase", self.data_backend == "supabase"),
                                             ("SUPABASE_URL / SUPABASE_PUBLISHABLE_KEY", self.supabase_configured),
                                             ("SCHEDULE_SIGNING_KEY", bool(self.schedule_signing_key)))
                       if not ok]
            if missing:
                raise ValueError("APP_ENV=production requires " + ", ".join(missing))
        return self

    @property
    def supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_publishable_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
