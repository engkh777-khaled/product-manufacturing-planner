"""Authentication with Supabase Auth (M05).

- Sign-in uses the password grant of Supabase Auth; the app keeps the access + refresh tokens in
  HTTP-only cookies (never readable by page JavaScript).
- Every request verifies the access token locally against the project's published signing keys (JWKS,
  ES256), refreshing it with the refresh token when it has expired.
- Identity and roles come from the `public.me()` RPC called *as the user*, so RLS decides what is visible.
- AUTH_MODE=disabled (development/test only) uses the fictional demo planner instead.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
import jwt
from fastapi import Request

from app.config import Settings, get_settings
from app.data import mock_data
from app.models import CurrentUser

ACCESS_COOKIE = "pmp_access"
REFRESH_COOKIE = "pmp_refresh"
SESSION_MAX_AGE = 60 * 60 * 24 * 7  # refresh-token cookie lifetime: 7 days

JWKS_TTL_SECONDS = 600
IDENTITY_TTL_SECONDS = 60


class AuthError(Exception):
    """Sign-in, refresh or verification failed. The message is safe to show to users."""


class LoginRequired(Exception):
    """Raised by the `require_user` dependency; turned into a redirect to /login."""


@dataclass
class Session:
    access_token: str
    refresh_token: str


def settings() -> Settings:
    """Indirection so tests can swap configuration."""
    return get_settings()


def _auth_url(path: str) -> str:
    return f"{settings().supabase_url.rstrip('/')}/auth/v1{path}"


def _headers(token: str | None = None) -> dict[str, str]:
    h = {"apikey": settings().supabase_publishable_key}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


async def _post(url: str, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(timeout=15) as client:
        return await client.post(url, **kwargs)


async def _get(url: str, **kwargs) -> httpx.Response:
    async with httpx.AsyncClient(timeout=15) as client:
        return await client.get(url, **kwargs)


# ------------------------------------------------------------------ Supabase Auth calls

async def sign_in(email: str, password: str) -> Session:
    try:
        r = await _post(_auth_url("/token?grant_type=password"), headers=_headers(),
                        json={"email": email, "password": password})
    except httpx.HTTPError as exc:
        raise AuthError("Authentication service is unreachable. Try again shortly.") from exc
    if r.status_code != 200:
        raise AuthError("Invalid email or password.")
    body = r.json()
    return Session(body["access_token"], body["refresh_token"])


async def refresh(refresh_token: str) -> Session:
    try:
        r = await _post(_auth_url("/token?grant_type=refresh_token"), headers=_headers(),
                        json={"refresh_token": refresh_token})
    except httpx.HTTPError as exc:
        raise AuthError("Authentication service is unreachable.") from exc
    if r.status_code != 200:
        raise AuthError("Session expired. Please sign in again.")
    body = r.json()
    return Session(body["access_token"], body["refresh_token"])


async def sign_out(access_token: str) -> None:
    try:
        await _post(_auth_url("/logout"), headers=_headers(access_token))
    except httpx.HTTPError:
        pass  # cookies are cleared regardless


# ------------------------------------------------------------------ Token verification

_jwks: tuple[float, jwt.PyJWKSet] | None = None


async def _jwk_set(force: bool = False) -> jwt.PyJWKSet:
    global _jwks
    if _jwks and not force and _jwks[0] > time.monotonic():
        return _jwks[1]
    try:
        r = await _get(_auth_url("/.well-known/jwks.json"), headers=_headers())
    except httpx.HTTPError as exc:
        raise AuthError("Cannot load token signing keys.") from exc
    if r.status_code != 200:
        raise AuthError("Cannot load token signing keys.")
    _jwks = (time.monotonic() + JWKS_TTL_SECONDS, jwt.PyJWKSet.from_dict(r.json()))
    return _jwks[1]


async def verify_access_token(token: str) -> dict:
    """Return verified claims. Raises jwt.ExpiredSignatureError / jwt.InvalidTokenError (bad token) or
    AuthError (signing keys unavailable — not the user's fault, so the session is kept)."""
    kid = jwt.get_unverified_header(token).get("kid")
    keys = await _jwk_set()
    try:
        key = keys[kid]
    except KeyError:
        keys = await _jwk_set(force=True)  # signing keys were rotated
        try:
            key = keys[kid]
        except KeyError as exc:
            raise jwt.InvalidTokenError("unknown signing key") from exc
    return jwt.decode(token, key.key, algorithms=[key.algorithm_name], audience="authenticated",
                      issuer=_auth_url(""))


# ------------------------------------------------------------------ Identity (roles via RLS)

_identity_cache: dict[str, tuple[float, CurrentUser]] = {}


async def fetch_identity(access_token: str) -> CurrentUser:
    cached = _identity_cache.get(access_token)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    url = f"{settings().supabase_url.rstrip('/')}/rest/v1/rpc/me"
    try:
        r = await _post(url, headers=_headers(access_token), json={})
    except httpx.HTTPError as exc:
        raise AuthError("Could not load your profile.") from exc
    if r.status_code != 200 or not r.json():
        raise AuthError("Could not load your profile.")
    me = r.json()
    user = CurrentUser(id=me["id"], email=me.get("email") or "", name=me.get("full_name") or me.get("email") or "User",
                       role=me.get("job_title") or ", ".join(me.get("roles") or []) or "No role assigned",
                       roles=me.get("roles") or [])
    if len(_identity_cache) > 512:
        _identity_cache.clear()
    _identity_cache[access_token] = (time.monotonic() + IDENTITY_TTL_SECONDS, user)
    return user


# ------------------------------------------------------------------ Request integration

def demo_user() -> CurrentUser:
    return mock_data.CURRENT_USER


async def resolve_session(request: Request) -> tuple[CurrentUser | None, Session | None, bool, str | None]:
    """Returns (user, new_session_to_store, clear_cookies, valid_access_token)."""
    if settings().auth_mode == "disabled":
        return demo_user(), None, False, None
    access = request.cookies.get(ACCESS_COOKIE)
    refresh_token = request.cookies.get(REFRESH_COOKIE)
    if not access and not refresh_token:
        return None, None, False, None
    new_session = None
    try:
        if not access:
            raise jwt.ExpiredSignatureError("no access token")
        await verify_access_token(access)
    except jwt.ExpiredSignatureError:
        if not refresh_token:
            return None, None, True, None
        try:
            new_session = await refresh(refresh_token)
            access = new_session.access_token
            await verify_access_token(access)
        except (AuthError, jwt.InvalidTokenError):
            return None, None, True, None
    except jwt.InvalidTokenError:
        return None, None, True, None  # forged, malformed or wrong audience: drop the cookies
    except AuthError:
        return None, None, False, None  # Auth service unreachable: keep cookies, ask again next request
    try:
        user = await fetch_identity(access)
    except AuthError:
        return None, new_session, False, None
    return user, new_session, False, access


def store_session(response, session: Session) -> None:
    secure = settings().app_env not in ("development", "test")
    for name, value in ((ACCESS_COOKIE, session.access_token), (REFRESH_COOKIE, session.refresh_token)):
        response.set_cookie(name, value, max_age=SESSION_MAX_AGE, httponly=True, samesite="lax",
                            secure=secure, path="/")


def clear_session(response) -> None:
    for name in (ACCESS_COOKIE, REFRESH_COOKIE):
        response.delete_cookie(name, path="/")


def require_user(request: Request) -> CurrentUser:
    """Router dependency: the signed-in user, or LoginRequired."""
    user = getattr(request.state, "user", None)
    if user is None:
        raise LoginRequired()
    return user


def safe_next(target: str | None) -> str:
    """Only allow local paths after login (no open redirects)."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return "/dashboard"
