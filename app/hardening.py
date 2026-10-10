"""Operational hardening (M12): security headers, request ids and access logs, a friendly error page, and simple
rate limits. Pure ASGI-level concerns — no business logic here."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import defaultdict, deque

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.config import get_settings

log = logging.getLogger("app.access")
error_log = logging.getLogger("app.errors")

# htmx and Alpine come from unpkg, fonts from Google. Alpine evaluates its x-* expressions with Function(),
# hence 'unsafe-eval'; templates use small inline handlers, hence 'unsafe-inline'. Everything else is locked down.
CSP = ("default-src 'self'; "
       "script-src 'self' https://unpkg.com 'unsafe-inline' 'unsafe-eval'; "
       "style-src 'self' https://fonts.googleapis.com 'unsafe-inline'; "
       "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'")

SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}


class RateLimiter:
    """Sliding-window limiter per key (in memory, per app instance)."""

    def __init__(self, limit: int, window_seconds: float):
        self.limit, self.window = limit, window_seconds
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                retry = int(self.window - (now - hits[0])) + 1
                raise HTTPException(429, f"Too many requests — try again in {retry} s.", headers={"Retry-After": str(retry)})
            hits.append(now)
            if len(self._hits) > 10_000:
                self._hits.clear()

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


login_limiter = RateLimiter(limit=10, window_seconds=60)          # per client IP + email
assistant_limiter = RateLimiter(limit=20, window_seconds=60)      # per user (AI cost and load control)


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")


ERROR_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Something went wrong</title>
<style>body{{font-family:system-ui,sans-serif;background:#0f141a;color:#dee3eb;display:grid;place-items:center;min-height:100vh;margin:0}}
main{{max-width:32rem;padding:2rem}}code{{color:#a8c8ff}}a{{color:#a8c8ff}}</style></head>
<body><main><h1>Something went wrong</h1><p>The error was logged. Nothing you submitted was partially saved by this page.</p>
<p>Reference: <code>{rid}</code></p><p><a href="/dashboard">Back to the dashboard</a></p></main></body></html>"""


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def harden(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = rid
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:  # unexpected: log with the reference, show a friendly page (never a traceback)
            error_log.exception("unhandled error %s %s [%s]", request.method, request.url.path, rid)
            response = HTMLResponse(ERROR_PAGE.format(rid=rid), status_code=500)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if get_settings().app_env == "production":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        response.headers["X-Request-ID"] = rid
        if not request.url.path.startswith("/static"):
            user = getattr(request.state, "user", None)
            log.info("%s %s %s %.0fms user=%s rid=%s", request.method, request.url.path, response.status_code,
                     (time.perf_counter() - started) * 1000, getattr(user, "email", None) or "-", rid)
        return response
