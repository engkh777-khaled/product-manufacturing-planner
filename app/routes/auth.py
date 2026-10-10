"""Sign-in / sign-out pages (M05)."""

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app import auth, hardening
from app.templating import templates

router = APIRouter()

DEMO_ACCOUNTS = ["planner", "supervisor", "stores", "purchasing", "engineering", "approver", "admin"]


def _login_page(request: Request, next_url: str, error: str | None = None, email: str = "", status: int = 200):
    show_demo = auth.settings().app_env == "development"
    return templates.TemplateResponse(request, "login.html", {
        "next": next_url, "error": error, "email": email,
        "demo_accounts": DEMO_ACCOUNTS if show_demo else [],
        "auth_disabled": auth.settings().auth_mode == "disabled",
    }, status_code=status)


@router.get("/login")
def login_form(request: Request, next: str = "/dashboard"):
    if getattr(request.state, "user", None) is not None:
        return RedirectResponse(auth.safe_next(next), status_code=303)
    return _login_page(request, auth.safe_next(next))


@router.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...), next: str = Form("/dashboard")):
    hardening.login_limiter.check(f"{hardening.client_ip(request)}|{email.strip().lower()}")  # slows password guessing
    target = auth.safe_next(next)
    if auth.settings().auth_mode == "disabled":
        return RedirectResponse(target, status_code=303)
    try:
        session = await auth.sign_in(email.strip(), password)
    except auth.AuthError as exc:
        return _login_page(request, target, str(exc), email=email.strip(), status=401)
    response = RedirectResponse(target, status_code=303)
    auth.store_session(response, session)
    return response


@router.post("/logout")
async def logout(request: Request):
    token = request.cookies.get(auth.ACCESS_COOKIE)
    if token:
        await auth.sign_out(token)
    response = RedirectResponse("/login", status_code=303)
    auth.clear_session(response)
    return response
