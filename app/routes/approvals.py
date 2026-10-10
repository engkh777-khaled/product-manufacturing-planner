"""Approvals & Audit (spec §12.8, M07)."""

from __future__ import annotations

import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.auth import require_user
from app.data import dataset, writes
from app.data import repository as repo
from app.data.dataset import current as ds
from app.data.supabase_dataset import ts
from app.models import CurrentUser
from app.templating import templates

router = APIRouter(dependencies=[Depends(require_user)])

AUDITED = ["approval_requests", "schedule_versions", "sales_orders", "sales_order_items", "bom_headers", "bom_items",
           "routing_headers", "routing_operations", "inventory_balances", "purchase_order_items", "user_roles",
           "machine_downtime", "app"]
NOISE = {"updated_at", "created_at", "id"}


def _short(value, names: dict | None = None) -> str:
    if names and isinstance(value, str) and value in names:
        return names[value]  # user ids (approved_by, requested_by…) shown as names
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= 40 else text[:37] + "…"


def changed_fields(before: dict | None, after: dict | None, names: dict | None = None) -> list[str]:
    """Human-readable field changes of one audit row."""
    if before and after:
        keys = [k for k in after if k not in NOISE and before.get(k) != after.get(k)]
        return [f"{k}: {_short(before.get(k), names)} → {_short(after.get(k), names)}" for k in keys][:8]
    if after and not before:
        return ["created"]
    if before and not after:
        return ["deleted"]
    return []


def _audit_rows(user: CurrentUser, entity: str, limit: int) -> tuple[list[dict], str]:
    if not dataset.is_supabase():
        rows = [{"at": e.at, "user": e.user, "actor_role": "app", "entity_type": "app", "entity_label": e.entity,
                 "action": e.action, "changes": [], "reason": e.reason} for e in reversed(repo.AUDIT_LOG)]
        return rows[:limit], "Demo data: showing this server's in-memory app events (database triggers need DATA_BACKEND=supabase)."
    if not ({"approver", "admin"} & set(user.roles)):
        return [], "Audit history is visible to approvers and admins only (enforced by Row Level Security)."
    result = writes.fetch_audit(limit, entity or None)
    if not result.ok:
        return [], result.message
    names = getattr(ds(), "PROFILES", {})
    rows = []
    for a in result.data:
        is_app_event = a["entity_type"] == "app"  # written by the app (blocked attempts, solver requests)
        rows.append({
            "at": ts(a["created_at"]),
            "user": names.get(a.get("user_id")) or ("system" if not a.get("user_id") else "unknown user"),
            "actor_role": a.get("actor_role") or "",
            "entity_type": a["entity_type"],
            "entity_label": a.get("entity_label") or ((a.get("after_data") or {}).get("entity", "") if is_app_event else ""),
            "action": a["action"],
            "changes": [] if is_app_event else changed_fields(a.get("before_data"), a.get("after_data"), names),
            "reason": a.get("reason") or "",
        })
    return rows, ""


@router.get("/approvals")
def approvals_page(request: Request, entity: str = "", limit: int = 100, msg: str = "",
                   user: CurrentUser = Depends(require_user)):
    limit = min(max(limit, 10), 500)
    audit, audit_note = _audit_rows(user, entity if entity in AUDITED else "", limit)
    return templates.TemplateResponse(request, "approvals.html", {
        **repo.approvals_overview(), "audit": audit, "audit_note": audit_note, "entities": AUDITED,
        "entity": entity, "limit": limit, "msg": msg[:300], "user": user,
        "is_supabase": dataset.is_supabase(),
    })


def _back(message: str) -> RedirectResponse:
    return RedirectResponse(f"/approvals?msg={quote(message)}", status_code=303)


@router.post("/approvals/{request_id}/decide")
def decide(request_id: str, decision: str = Form(...), comments: str = Form(""),
           user: CurrentUser = Depends(require_user)):
    if decision not in ("Approved", "Rejected"):
        return _back("Choose Approve or Reject.")
    result = repo.request_decision(request_id, decision, user, comments.strip()[:1000])
    return _back(result["message"])


@router.post("/approvals/{request_id}/withdraw")
def withdraw(request_id: str, user: CurrentUser = Depends(require_user)):
    return _back(repo.withdraw_request(request_id, user)["message"])


@router.post("/approvals/submit")
def submit(version: str = Form(...), notes: str = Form(""), user: CurrentUser = Depends(require_user)):
    if not user.can_submit_plans:
        return _back(f"{user.role} cannot submit plans for approval (planner role required).")
    result = repo.submit_for_approval(version, notes.strip()[:1000], user)
    if "error" in result:
        return _back(result["error"])
    number = result.get("request_number")
    return _back(f"v{result['version'].version_number} submitted" + (f" as {number}" if number else "") +
                 " — awaiting an authorized approver.")
