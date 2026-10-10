from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app import auth
from app.routes import assistant, dashboard, engineering, health, orders, workbench
from app.routes import auth as auth_routes

app = FastAPI(title="Product Manufacturing Planner", version="0.3.0")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

for module in (health, auth_routes, dashboard, workbench, orders, engineering, assistant):
    app.include_router(module.router)


@app.middleware("http")
async def auth_session(request: Request, call_next):
    """Attach the signed-in user to request.state and keep the session cookies fresh."""
    request.state.user = None
    new_session, clear = None, False
    if not request.url.path.startswith(("/static", "/healthz")):
        request.state.user, new_session, clear = await auth.resolve_session(request)
    response = await call_next(request)
    if new_session:
        auth.store_session(response, new_session)
    elif clear:
        auth.clear_session(response)
    return response


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
