from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request

from app import assistant
from app.auth import require_user
from app.data.dataset import current as ds
from app.data import repository as repo
from app.models import CurrentUser
from app.templating import templates

router = APIRouter(dependencies=[Depends(require_user)])


@router.get("/assistant")
def assistant_page(request: Request, order: str = "SO-1005"):
    detail = repo.get_order(order) or repo.get_order("SO-1005")
    o = detail["o"]
    question = f"Can we deliver sales order {o.order_number} by {o.requested_delivery_date.strftime('%d %B')}?"
    return templates.TemplateResponse(request, "assistant.html", {
        "detail": detail,
        "plan_order": repo.get_order("SO-1005"),
        "question": question,
        "first_reply": None if o.order_number == "SO-1005" else assistant.respond(question),
        "cmp": repo.scenario_comparison(),
        "po": next((p for p in ds().PURCHASE_ORDERS if p.po_number == "PO-8821"), None),
        "bottleneck": repo.bottleneck(),
        "quick_prompts": ds().ASSISTANT_QUICK_PROMPTS,
        "approval": next(iter(repo.approval_requests()), None),
        "approver_role": ds().APPROVER_ROLE,
    })


@router.post("/assistant/message")
def message(request: Request, prompt: str = Form(...)):
    prompt = prompt.strip()[:500]
    return templates.TemplateResponse(request, "partials/chat_exchange.html", {
        "prompt": prompt,
        "reply": assistant.respond(prompt) if prompt else None,
        "now": datetime.now(),
    })


@router.post("/assistant/submit")
def submit(request: Request, version: str = Form(...), notes: str = Form(""),
           expedite_confirmed: bool = Form(False), overtime_confirmed: bool = Form(False),
           user: CurrentUser = Depends(require_user)):
    if not user.can_submit_plans:
        return templates.TemplateResponse(request, "partials/approval_submitted.html",
                                          {"error": f"{user.role} cannot submit plans for approval (planner role required)."})
    # Server-side gate: both human confirmations are required, regardless of what the UI allowed.
    if not (expedite_confirmed and overtime_confirmed):
        return templates.TemplateResponse(request, "partials/approval_submitted.html",
                                          {"error": "Both planner verifications must be confirmed before submission."})
    result = repo.submit_for_approval(version, notes.strip()[:1000], user)
    return templates.TemplateResponse(request, "partials/approval_submitted.html", {"result": result, "error": None})
