"""Health check: app status plus Supabase reachability (M03)."""

from __future__ import annotations

import time

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.config import Settings, get_settings

router = APIRouter()


def probe_supabase(settings: Settings) -> dict:
    """Call the Supabase Auth health endpoint with the publishable key."""
    if not settings.supabase_configured:
        return {"configured": False, "reachable": None}
    url = f"{settings.supabase_url.rstrip('/')}/auth/v1/health"
    started = time.perf_counter()
    try:
        r = httpx.get(url, headers={"apikey": settings.supabase_publishable_key},
                      timeout=settings.health_timeout_seconds)
        latency_ms = round((time.perf_counter() - started) * 1000)
        if r.status_code == 200:
            return {"configured": True, "reachable": True, "latency_ms": latency_ms,
                    "auth_version": r.json().get("version")}
        return {"configured": True, "reachable": False, "latency_ms": latency_ms,
                "detail": f"HTTP {r.status_code}"}
    except httpx.HTTPError as exc:
        return {"configured": True, "reachable": False, "detail": type(exc).__name__}


@router.get("/healthz", tags=["ops"])
def healthz(request: Request, settings: Settings = Depends(get_settings)):
    supabase = probe_supabase(settings)
    # Supabase is only required when it serves the data; otherwise report it but stay "ok".
    degraded = supabase["reachable"] is False or (
        settings.data_backend == "supabase" and not supabase["configured"])
    body = {
        "status": "degraded" if degraded else "ok",
        "version": request.app.version,
        "environment": settings.app_env,
        "data_backend": settings.data_backend,
        "supabase": supabase,
    }
    return JSONResponse(body, status_code=503 if degraded else 200)


@router.get("/livez", tags=["ops"])
def livez(request: Request):
    """Liveness for container health checks: the process answers; no external calls (readiness is /healthz)."""
    return {"status": "alive", "version": request.app.version}
