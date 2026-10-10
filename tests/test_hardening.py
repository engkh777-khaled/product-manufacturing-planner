"""M12 hardening: security headers, request ids, friendly error page, rate limits, production configuration."""

from __future__ import annotations

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import hardening
from app.config import Settings
from app.main import app


def test_security_headers_and_request_id_on_every_response():
    client = TestClient(app)
    for path in ("/dashboard", "/login", "/static/css/app.css", "/nope"):
        r = client.get(path)
        assert r.headers["X-Frame-Options"] == "DENY" and r.headers["X-Content-Type-Options"] == "nosniff"
        assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
        assert "object-src 'none'" in r.headers["Content-Security-Policy"]
        assert len(r.headers["X-Request-ID"]) >= 8
    assert client.get("/dashboard", headers={"X-Request-ID": "trace-123"}).headers["X-Request-ID"] == "trace-123"
    assert "Strict-Transport-Security" not in client.get("/dashboard").headers      # HSTS only in production


def test_unexpected_errors_show_a_reference_not_a_traceback():
    router = APIRouter()

    @router.get("/__boom")
    def boom():
        raise RuntimeError("secret internals: password=hunter2")
    app.include_router(router)
    try:
        r = TestClient(app, raise_server_exceptions=False).get("/__boom")
        assert r.status_code == 500 and "Something went wrong" in r.text
        assert r.headers["X-Request-ID"] in r.text and "hunter2" not in r.text and "Traceback" not in r.text
    finally:
        app.router.routes[:] = [rt for rt in app.router.routes if getattr(rt, "path", "") != "/__boom"]


def test_rate_limiter_window():
    limiter = hardening.RateLimiter(limit=2, window_seconds=60)
    limiter.check("k")
    limiter.check("k")
    with pytest.raises(Exception) as exc:
        limiter.check("k")
    assert exc.value.status_code == 429 and exc.value.headers["Retry-After"]
    limiter.check("other")                                                 # keys are independent


def test_login_is_rate_limited_per_ip_and_email():
    client = TestClient(app, follow_redirects=False)
    for _ in range(10):
        client.post("/login", data={"email": "planner@example.com", "password": "x"})
    r = client.post("/login", data={"email": "planner@example.com", "password": "x"})
    assert r.status_code == 429 and "Too many requests" in r.text


def test_production_refuses_unsafe_configuration():
    with pytest.raises(ValidationError, match="APP_ENV=production requires DATA_BACKEND=supabase"):
        Settings(_env_file=None, app_env="production", auth_mode="supabase", data_backend="mock")
    with pytest.raises(ValidationError, match="SCHEDULE_SIGNING_KEY"):
        Settings(_env_file=None, app_env="production", auth_mode="supabase", data_backend="supabase",
                 supabase_url="https://x.supabase.co", supabase_publishable_key="pk", schedule_signing_key=None)
    with pytest.raises(ValidationError, match="AUTH_MODE=disabled is only allowed"):
        Settings(_env_file=None, app_env="production", auth_mode="disabled")
    ok = Settings(_env_file=None, app_env="production", auth_mode="supabase", data_backend="supabase",
                  supabase_url="https://x.supabase.co", supabase_publishable_key="pk", schedule_signing_key="k" * 64)
    assert ok.app_env == "production"


def test_liveness_needs_no_login_or_database():
    r = TestClient(app).get("/livez")
    assert r.status_code == 200 and r.json()["status"] == "alive"
