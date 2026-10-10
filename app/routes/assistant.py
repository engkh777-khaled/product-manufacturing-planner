from datetime import datetime

from fastapi import APIRouter, Form, Request

from app import assistant
from app.data import mock_data as db
from app.data import repository as repo
from app.templating import templates

router = APIRouter()


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
        "po": next(p for p in db.PURCHASE_ORDERS if p.po_number == "PO-8821"),
        "bottleneck": repo.bottleneck(),
        "quick_prompts": db.ASSISTANT_QUICK_PROMPTS,
        "approval": repo.approval_requests()[0],
        "approver_role": db.APPROVER_ROLE,
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
           expedite_confirmed: bool = Form(False), overtime_confirmed: bool = Form(False)):
    # Server-side gate: both human confirmations are required, regardless of what the UI allowed.
    if not (expedite_confirmed and overtime_confirmed):
        return templates.TemplateResponse(request, "partials/approval_submitted.html",
                                          {"error": "Both planner verifications must be confirmed before submission."})
    result = repo.submit_for_approval(version, notes.strip()[:1000])
    return templates.TemplateResponse(request, "partials/approval_submitted.html", {"result": result, "error": None})
