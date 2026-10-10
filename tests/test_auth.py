"""M05: sign-in, sessions and role checks against a mocked Supabase Auth (no network).

A real ES256 key pair is generated per test session, so token signing and verification are exercised for real.
"""

import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import auth
from app.config import Settings
from app.data import mock_data
from app.main import app

BASE = "https://example.supabase.co"
ISSUER = f"{BASE}/auth/v1"
PASSWORD = "correct-horse"

_key = ec.generate_private_key(ec.SECP256R1())
_other_key = ec.generate_private_key(ec.SECP256R1())
_jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(_key.public_key()))
_jwk.update({"kid": "test-kid", "alg": "ES256", "use": "sig"})

USERS = {
    "planner@example.com": {"id": "11111111-1111-1111-1111-111111111111", "full_name": "Sarah Chen",
                            "job_title": "Sr. Production Planner", "roles": ["planner"]},
    "approver@example.com": {"id": "22222222-2222-2222-2222-222222222222", "full_name": "Marcus Vance",
                             "job_title": "Plant Operations Director", "roles": ["approver"]},
}


def make_token(email: str, expires_in: int = 3600, key=_key, issued_offset: int = 0) -> str:
    now = int(time.time())
    claims = {"sub": USERS[email]["id"], "email": email, "aud": "authenticated", "role": "authenticated",
              "iss": ISSUER, "iat": now + issued_offset, "exp": now + expires_in}
    return jwt.encode(claims, key, algorithm="ES256", headers={"kid": "test-kid"})


class FakeSupabaseAuth:
    """Answers the handful of Supabase endpoints app.auth calls."""

    def __init__(self):
        self.refresh_tokens: dict[str, str] = {}
        self.calls: list[str] = []
        self.issued = 0

    def session(self, email):
        self.issued += 1
        refresh = f"refresh-{self.issued}"
        self.refresh_tokens[refresh] = email
        return {"access_token": make_token(email), "refresh_token": refresh, "expires_in": 3600}

    async def post(self, url, headers=None, json=None, **_):
        self.calls.append(url)
        if url.endswith("grant_type=password"):
            if json["email"] in USERS and json["password"] == PASSWORD:
                return httpx.Response(200, json=self.session(json["email"]))
            return httpx.Response(400, json={"error_code": "invalid_credentials"})
        if url.endswith("grant_type=refresh_token"):
            email = self.refresh_tokens.pop(json["refresh_token"], None)
            return httpx.Response(200, json=self.session(email)) if email else httpx.Response(400, json={})
        if url.endswith("/rest/v1/rpc/me"):
            claims = jwt.decode(headers["Authorization"].split()[1], options={"verify_signature": False})
            u = USERS[claims["email"]]
            return httpx.Response(200, json={"id": u["id"], "email": claims["email"], "full_name": u["full_name"],
                                             "job_title": u["job_title"], "roles": u["roles"]})
        if url.endswith("/logout"):
            return httpx.Response(204)
        raise AssertionError(f"unexpected POST {url}")

    async def get(self, url, headers=None, **_):
        assert url.endswith("/.well-known/jwks.json")
        return httpx.Response(200, json={"keys": [_jwk]})


@pytest.fixture
def fake(monkeypatch):
    settings = Settings(_env_file=None, app_env="test", auth_mode="supabase",
                        supabase_url=BASE, supabase_publishable_key="sb_publishable_test")
    server = FakeSupabaseAuth()
    monkeypatch.setattr(auth, "settings", lambda: settings)
    monkeypatch.setattr(auth, "_post", server.post)
    monkeypatch.setattr(auth, "_get", server.get)
    monkeypatch.setattr(auth, "_jwks", None)
    auth._identity_cache.clear()
    return server


@pytest.fixture
def client(fake):
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def reset_approvals():
    yield
    for r in mock_data.APPROVAL_REQUESTS:
        r.status, r.requested_by = "Pending", "Sarah Chen"


def sign_in(client, email="planner@example.com", password=PASSWORD, next_url="/dashboard"):
    return client.post("/login", data={"email": email, "password": password, "next": next_url})


# ------------------------------------------------------------------ access control

def test_pages_redirect_to_login_without_session(client):
    r = client.get("/orders?tab=at_risk")
    assert r.status_code == 303
    assert r.headers["location"] == "/login?next=/orders%3Ftab%3Dat_risk"


def test_htmx_fragment_without_session_gets_hx_redirect(client):
    r = client.get("/orders/table", headers={"HX-Request": "true", "HX-Current-URL": "http://testserver/orders"})
    assert r.status_code == 401
    assert r.headers["HX-Redirect"] == "/login?next=/orders"


def test_health_and_login_are_public(client):
    assert client.get("/login").status_code == 200
    assert client.get("/healthz").status_code in (200, 503)  # depends on Supabase reachability, never a redirect


# ------------------------------------------------------------------ sign-in / sign-out

def test_wrong_password_shows_generic_error(client):
    r = sign_in(client, password="nope")
    assert r.status_code == 401
    assert "Invalid email or password." in r.text
    assert auth.ACCESS_COOKIE not in r.cookies


def test_sign_in_sets_http_only_cookies_and_shows_user(client):
    r = sign_in(client, next_url="/orders")
    assert r.status_code == 303 and r.headers["location"] == "/orders"
    set_cookie = r.headers.get_list("set-cookie")
    assert any(c.startswith(f"{auth.ACCESS_COOKIE}=") and "HttpOnly" in c and "SameSite=lax" in c for c in set_cookie)
    page = client.get("/dashboard")
    assert page.status_code == 200
    assert "Sarah Chen" in page.text and "Sr. Production Planner" in page.text and "planner@example.com" in page.text


def test_open_redirect_is_blocked(client):
    r = sign_in(client, next_url="//evil.example.com/steal")
    assert r.headers["location"] == "/dashboard"


def test_expired_access_token_is_refreshed(client, fake):
    sign_in(client)
    refresh = client.cookies.get(auth.REFRESH_COOKIE)
    client.cookies.set(auth.ACCESS_COOKIE, make_token("planner@example.com", expires_in=-60))
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert any(u.endswith("grant_type=refresh_token") for u in fake.calls)
    assert client.cookies.get(auth.REFRESH_COOKIE) != refresh  # rotated


def test_small_clock_skew_does_not_sign_the_user_out(client, fake):
    # Seen live (M09): this PC's clock ran behind Supabase, so a fresh token's iat was a few seconds "in the future".
    sign_in(client)
    client.cookies.set(auth.ACCESS_COOKIE, make_token("planner@example.com", issued_offset=5))
    assert client.get("/dashboard").status_code == 200


def test_forged_token_is_rejected_and_cookies_cleared(client):
    client.cookies.set(auth.ACCESS_COOKIE, make_token("planner@example.com", key=_other_key))
    r = client.get("/dashboard")
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert any(c.startswith(f"{auth.ACCESS_COOKIE}=\"\"") or "Max-Age=0" in c for c in r.headers.get_list("set-cookie"))


def test_sign_out_clears_session(client, fake):
    sign_in(client)
    r = client.post("/logout")
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert any(u.endswith("/logout") for u in fake.calls)
    assert client.get("/dashboard").status_code == 303


# ------------------------------------------------------------------ role checks (spec §5)

def test_planner_cannot_commit_plan(client, reset_approvals):
    sign_in(client)
    r = client.post("/approvals/APR-1042/decision", data={"decision": "Commit"})
    assert "cannot commit" in r.text
    assert mock_data.APPROVAL_REQUESTS[0].status == "Pending"


def test_approver_can_commit_someone_elses_plan(client, reset_approvals):
    sign_in(client, email="approver@example.com")
    r = client.post("/approvals/APR-1042/decision", data={"decision": "Commit"})
    assert "approved" in r.text
    assert mock_data.APPROVAL_REQUESTS[0].status == "Approved"


def test_approver_cannot_approve_own_submission(client, reset_approvals):
    mock_data.APPROVAL_REQUESTS[0].requested_by = "Marcus Vance"
    sign_in(client, email="approver@example.com")
    r = client.post("/approvals/APR-1042/decision", data={"decision": "Commit"})
    assert "submitted yourself" in r.text
    assert mock_data.APPROVAL_REQUESTS[0].status == "Pending"


def test_only_planners_can_submit_plans(client):
    sign_in(client, email="approver@example.com")
    r = client.post("/assistant/submit", data={"version": "v4.3", "expedite_confirmed": "true",
                                               "overtime_confirmed": "true"})
    assert "cannot submit plans" in r.text


def test_auth_cannot_be_disabled_in_production():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="production", auth_mode="disabled")
