import logging
import os
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app import auth, hardening
from app.data import dataset, supabase_dataset
from app.routes import approvals, assistant, dashboard, engineering, health, inventory, orders, resources, scenarios, workbench
from app.routes import auth as auth_routes

# App loggers (access, errors, assistant tools) next to uvicorn's own; LOG_LEVEL overrides (M12).
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(title="Product Manufacturing Planner", version="0.4.0")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

for module in (health, auth_routes, dashboard, workbench, scenarios, orders, inventory, resources, engineering, approvals, assistant):
    app.include_router(module.router)


@app.middleware("http")
async def auth_session(request: Request, call_next):
    """Attach the signed-in user to request.state and keep the session cookies fresh."""
    request.state.user = None
    new_session, clear, access = None, False, None
    if not request.url.path.startswith(("/static", "/healthz", "/livez")):
        request.state.user, new_session, clear, access = await auth.resolve_session(request)

    tokens = None
    settings = auth.settings()
    if settings.data_backend == "supabase" and request.state.user is not None and access:
        try:
            snapshot = await supabase_dataset.load_dataset(settings.supabase_url, settings.supabase_publishable_key, access)
        except supabase_dataset.SnapshotError as exc:
            # Never fall back to demo data silently: a planner must know the numbers are unavailable.
            return HTMLResponse(f"<h1>Plant data unavailable</h1><p>{exc}. Try again shortly.</p>"
                                f"<p><a href='/healthz'>Check /healthz</a></p>", status_code=503)
        tokens = dataset.use(snapshot, access)
    try:
        response = await call_next(request)
    finally:
        if tokens:
            dataset.reset(tokens)
    if new_session:
        auth.store_session(response, new_session)
    elif clear:
        auth.clear_session(response)
    return response


# Outermost layer (registered last): security headers, request ids, access log, friendly 500 page (M12).
hardening.install(app)


@app.exception_handler(auth.LoginRequired)
async def login_required(request: Request, exc: auth.LoginRequired):
    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    login_url = f"/login?next={quote(target)}"
    if request.headers.get("HX-Request"):
        # HTMX fragment requests: tell htmx to navigate the whole page to the login screen.
        current = request.headers.get("HX-Current-URL", "")
        page = "/" + current.split("/", 3)[3] if current.count("/") >= 3 else "/dashboard"
        return Response(status_code=401, headers={"HX-Redirect": f"/login?next={quote(auth.safe_next(page))}"})
    return RedirectResponse(login_url, status_code=303)


@app.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    return RedirectResponse("/dashboard")
