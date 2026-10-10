import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import app
from app.routes import health

client = TestClient(app)


def use_settings(**values) -> None:
    settings = Settings(_env_file=None, **values)  # ignore the developer's .env
    app.dependency_overrides[get_settings] = lambda: settings


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.clear()


SUPABASE = {"supabase_url": "https://example.supabase.co", "supabase_publishable_key": "sb_publishable_test"}


def test_ok_without_supabase_on_mock_backend():
    use_settings()
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["data_backend"] == "mock"
    assert body["supabase"] == {"configured": False, "reachable": None}


def test_ok_when_supabase_reachable(monkeypatch):
    use_settings(**SUPABASE)
    calls = {}

    def fake_get(url, headers, timeout):
        calls["url"], calls["apikey"] = url, headers["apikey"]
        return httpx.Response(200, json={"version": "v2.0.0", "name": "GoTrue"})

    monkeypatch.setattr(health.httpx, "get", fake_get)
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["supabase"]["reachable"] is True
    assert calls == {"url": "https://example.supabase.co/auth/v1/health", "apikey": "sb_publishable_test"}


def test_degraded_when_supabase_unreachable(monkeypatch):
    use_settings(**SUPABASE)

    def boom(*args, **kwargs):
        raise httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(health.httpx, "get", boom)
    r = client.get("/healthz")
    assert r.status_code == 503
    assert r.json()["status"] == "degraded"
    assert r.json()["supabase"]["detail"] == "ConnectTimeout"


def test_degraded_when_supabase_backend_selected_but_not_configured():
    use_settings(data_backend="supabase", auth_mode="supabase")
    r = client.get("/healthz")
    assert r.status_code == 503


def test_secret_key_never_leaks(monkeypatch):
    secret = "sb_secret_do_not_leak"
    settings = Settings(_env_file=None, **SUPABASE, supabase_secret_key=secret)
    assert secret not in repr(settings) and secret not in str(settings.model_dump())

    use_settings(**SUPABASE, supabase_secret_key=secret)
    monkeypatch.setattr(health.httpx, "get", lambda *a, **k: httpx.Response(200, json={}))
    assert secret not in client.get("/healthz").text
