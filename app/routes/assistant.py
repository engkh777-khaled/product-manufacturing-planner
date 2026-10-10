from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request

from app import assistant
from app.agent import loop as agent
from app.agent import tools as agent_tools
from app.auth import require_user
from app.config import get_settings
from app.data import dataset
from app.data.dataset import current as ds
from app.data import repository as repo
from app.models import CurrentUser
from app.templating import templates

router = APIRouter(dependencies=[Depends(require_user)])

AGENT_SUGGESTIONS = [
    "Can we deliver SO-1005 by 30 November?",
    "Which orders are most at risk?",
    "Which machine is the current bottleneck?",
    "What happens if PO-8821 is delayed by 5 days?",
    "Which components are blocking production?",
    "What changed between the last two schedules?",
]


@router.get("/assistant")
def assistant_page(request: Request, order: str = "SO-1005", version: str | None = None,
                   user: CurrentUser = Depends(require_user)):
    detail = repo.get_order(order) or repo.get_order("SO-1005")
    o = detail["o"]
    question = f"Can we deliver sales order {o.order_number} by {o.requested_delivery_date.strftime('%d %B')}?"
    agent_mode = get_settings().assistant_enabled
    conv = agent.conversation(user.id, None, o.order_number, version) if agent_mode else None
    return templates.TemplateResponse(request, "assistant.html", {
        "detail": detail,
        "plan_order": repo.get_order("SO-1005"),
        "question": question,
        "first_reply": None if o.order_number == "SO-1005" else assistant.respond(question),
        "cmp": repo.scenario_comparison(),
        "po": next((p for p in ds().PURCHASE_ORDERS if p.po_number == "PO-8821"), None),
        "bottleneck": repo.bottleneck(),
        "quick_prompts": AGENT_SUGGESTIONS if agent_mode else ds().ASSISTANT_QUICK_PROMPTS,
        "approval": next(iter(repo.approval_requests()), None),
        "approver_role": ds().APPROVER_ROLE,
        "agent_mode": agent_mode,
        "conv": conv,
        "model": get_settings().assistant_model if agent_mode else None,
        "version_key": version or repo.baseline().key,
        "user": user,
    })


@router.post("/assistant/message")
def message(request: Request, prompt: str = Form(...), conv_id: str = Form(""), order: str = Form(""),
            version: str = Form(""), user: CurrentUser = Depends(require_user)):
    prompt = prompt.strip()[:2000]
    if get_settings().assistant_enabled:
        if not prompt:
            return templates.TemplateResponse(request, "partials/agent_turn.html", {"turn": None})
        conv = agent.conversation(user.id, conv_id or None, order or None, version or None)
        ctx = agent_tools.ToolContext(user=user, token=dataset.access_token(), conversation_id=conv.id,
                                      selected_order=conv.order, selected_version=conv.version)
        turn = agent.run_turn(conv, prompt, ctx)
        actions = [agent_tools._pending[c.action_id] for c in turn.trace if c.action_id in agent_tools._pending]
        return templates.TemplateResponse(request, "partials/agent_turn.html", {
            "turn": turn, "actions": actions, "user": user, "render": agent.render})
    return templates.TemplateResponse(request, "partials/chat_exchange.html", {
        "prompt": prompt[:500],
        "reply": assistant.respond(prompt[:500]) if prompt else None,
        "now": datetime.now(),
    })


@router.post("/assistant/actions/{action_id}/confirm")
def confirm_action(request: Request, action_id: str, user: CurrentUser = Depends(require_user)):
    """The human confirms a consequential action proposed by the assistant (spec §11.3 rule 12)."""
    try:
        action = agent_tools.execute_action(action_id, user)
    except agent_tools.ToolError as exc:
        return templates.TemplateResponse(request, "partials/agent_action.html", {"action": None, "error": str(exc)})
    return templates.TemplateResponse(request, "partials/agent_action.html", {"action": action, "error": None})


@router.post("/assistant/actions/{action_id}/dismiss")
def dismiss_action(request: Request, action_id: str, user: CurrentUser = Depends(require_user)):
    agent_tools.dismiss_action(action_id, user)
    action = agent_tools._pending.get(action_id)
    return templates.TemplateResponse(request, "partials/agent_action.html", {
        "action": action if action and action.user_id == user.id else None, "error": None})


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
    if "error" in result:
        return templates.TemplateResponse(request, "partials/approval_submitted.html", {"error": result["error"]})
    return templates.TemplateResponse(request, "partials/approval_submitted.html", {"result": result, "error": None})
