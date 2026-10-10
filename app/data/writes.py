"""Writes to Supabase as the signed-in user (M06).

Every call forwards the user's access token to PostgREST, so Row Level Security decides what is allowed —
the app checks roles too, but the database is the final authority (spec §5). Errors come back as
WriteResult messages that are safe to show in the UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt

from app import auth
from app.data import dataset, supabase_dataset

ERROR_MESSAGES = {
    "42501": "You don't have permission to make this change.",
    "23505": "That record already exists.",
    "23503": "This record is referenced elsewhere or points to something missing.",
    "PGRST116": "Record not found.",
}


@dataclass
class WriteResult:
    ok: bool
    message: str
    data: Any = field(default=None)


def _rest(method: str, path: str, *, json=None, params=None, prefer: str = "return=representation",
          token: str | None = None, timeout: float = 20) -> WriteResult:
    token = token or dataset.access_token()   # background jobs pass the token they were started with
    settings = auth.settings()
    if not token or not settings.supabase_configured:
        return WriteResult(False, "Saving requires DATA_BACKEND=supabase and a signed-in user.")
    headers = {"apikey": settings.supabase_publishable_key, "Authorization": f"Bearer {token}",
               "Content-Type": "application/json", "Prefer": prefer}
    try:
        r = httpx.request(method, f"{settings.supabase_url.rstrip('/')}/rest/v1/{path}", headers=headers,
                          json=json, params=params, timeout=timeout)
    except httpx.HTTPError:
        return WriteResult(False, "Supabase is unreachable. Nothing was saved.")
    if r.status_code >= 400:
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        code = body.get("code", "")
        if code == "23514" or code == "P0001":  # CHECK / raised by our functions: the message explains the rule
            return WriteResult(False, f"Rejected: {body.get('message', 'a data rule was violated')}.")
        message = body.get("message") or ""
        if code in ("42501", "P0002") and message and not any(
                generic in message for generic in ("permission denied", "row-level security")):
            return WriteResult(False, f"{message}.")  # our workflow functions explain why (e.g. self-approval)
        return WriteResult(False, ERROR_MESSAGES.get(code, f"Save failed ({r.status_code}: {body.get('message', 'error')})."))
    if method != "GET" and path != "rpc/planning_snapshot":
        supabase_dataset.invalidate()  # every user's next page load sees the change
    data = r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else None
    return WriteResult(True, "Saved.", data)


def _patch_one(path: str, filters: dict, values: dict, not_found: str) -> WriteResult:
    result = _rest("PATCH", path, json=values, params=filters)
    if result.ok and not result.data:
        # RLS hides rows the user may not change: PostgREST then updates nothing instead of raising.
        return WriteResult(False, not_found)
    return result


def _user_id() -> str | None:
    token = dataset.access_token()
    return jwt.decode(token, options={"verify_signature": False}).get("sub") if token else None  # verified in middleware


# ------------------------------------------------------------------ sales orders

def save_draft_order(payload: dict) -> WriteResult:
    result = _rest("POST", "rpc/save_draft_sales_order", json={"p": payload}, prefer="return=minimal")
    if result.ok:
        result.message = f"{payload['order_number']} saved as Draft."
    return result


def confirm_order(order_number: str) -> WriteResult:
    result = _rest("POST", "rpc/confirm_sales_order", json={"p_order_number": order_number}, prefer="return=minimal")
    if result.ok:
        result.message = f"{order_number} confirmed."
    return result


def delete_draft_order(order_number: str) -> WriteResult:
    result = _rest("DELETE", "sales_orders", params={"order_number": f"eq.{order_number}", "status": "eq.Draft"})
    if result.ok and not result.data:
        return WriteResult(False, f"{order_number} is not a Draft you may delete.")
    if result.ok:
        result.message = f"{order_number} deleted."
    return result


# ------------------------------------------------------------------ inventory & purchasing

def update_inventory(balance_id: str, on_hand: float, reserved: float, quality_hold: float) -> WriteResult:
    return _patch_one("inventory_balances", {"id": f"eq.{balance_id}"},
                      {"on_hand_quantity": on_hand, "reserved_quantity": reserved, "quality_hold_quantity": quality_hold},
                      "You don't have permission to adjust inventory (stores or admin role required).")


def update_po_line(line_id: str, values: dict) -> WriteResult:
    return _patch_one("purchase_order_items", {"id": f"eq.{line_id}"}, values,
                      "You don't have permission to update purchase order lines.")


# ------------------------------------------------------------------ engineering revisions

def submit_bom_for_review(bom_id: str) -> WriteResult:
    return _patch_one("bom_headers", {"id": f"eq.{bom_id}", "status": "eq.Draft"}, {"status": "Under Review"},
                      "Only Draft revisions can be submitted, by engineering or admin.")


def return_bom_to_draft(bom_id: str) -> WriteResult:
    return _patch_one("bom_headers", {"id": f"eq.{bom_id}", "status": "eq.Under Review"}, {"status": "Draft"},
                      "Only revisions Under Review can be returned to Draft.")


def approve_bom(bom_id: str) -> WriteResult:
    result = _rest("POST", "rpc/approve_bom_revision", json={"p_bom_header_id": bom_id}, prefer="return=minimal")
    if result.ok:
        result.message = "Revision approved; the previous approved revision is now Superseded."
    return result


# ------------------------------------------------------------------ approvals & audit

def submit_plan(version_number: str, comments: str = "") -> WriteResult:
    """Planner: Draft/Proposed + validated version → Proposed, new Pending approval request (M07)."""
    result = _rest("POST", "rpc/submit_plan_for_approval",
                   json={"p_version_number": version_number, "p_comments": comments or None})
    if result.ok:
        result.message = f"Submitted as {result.data} — awaiting an authorized approver."
    return result


def decide_plan(request_number: str, decision: str, comments: str = "") -> WriteResult:
    """Approver: approve (version becomes the baseline, old baseline Superseded) or reject (M07)."""
    result = _rest("POST", "rpc/decide_plan_approval", prefer="return=minimal",
                   json={"p_request_number": request_number, "p_decision": decision, "p_comments": comments or None})
    if result.ok:
        result.message = (f"{request_number} approved — the plan is now the baseline; the previous baseline is Superseded."
                          if decision == "Approved" else f"{request_number} rejected — the baseline is unchanged.")
    return result


def withdraw_plan(request_number: str) -> WriteResult:
    result = _rest("POST", "rpc/withdraw_plan_approval", prefer="return=minimal",
                   json={"p_request_number": request_number})
    if result.ok:
        result.message = f"{request_number} withdrawn; the version is back to Draft."
    return result


def record_mrp_run(payload: dict) -> WriteResult:
    """Planner/admin: store an MRP run and replace the open material alerts (M08)."""
    result = _rest("POST", "rpc/record_mrp_run", json={"p": payload})
    if result.ok:
        result.message = f"MRP run #{result.data} saved with {len(payload['alerts'])} alerts."
    return result


# ------------------------------------------------------------------ resources & calendars (supervisor/admin, M09)

def create_downtime(values: dict, machine_code: str) -> WriteResult:
    result = _rest("POST", "machine_downtime", json={**values, "created_by": _user_id()})
    if result.ok:
        result.message = f"Downtime on {machine_code} recorded ({values['status']})."
    return result


def set_downtime_status(downtime_id: str, status: str) -> WriteResult:
    result = _patch_one("machine_downtime", {"id": f"eq.{downtime_id}"}, {"status": status},
                        "Downtime not found or you may not change it.")
    if result.ok:
        result.message = f"Downtime marked {status}."
    return result


def create_calendar_exception(values: dict) -> WriteResult:
    result = _rest("POST", "calendar_exceptions", json=values)
    if result.ok:
        result.message = f"{values['exception_type'].capitalize()} '{values['description']}' added."
    return result


def set_exception_approval(exception_id: str, approved: bool) -> WriteResult:
    result = _patch_one("calendar_exceptions", {"id": f"eq.{exception_id}"}, {"is_approved": approved},
                        "Calendar entry not found or you may not change it.")
    if result.ok:
        result.message = "Overtime approved — the scheduler will use it." if approved else "Approval withdrawn."
    return result


def fetch_snapshot(token: str) -> WriteResult:
    """A fresh RLS-filtered planning snapshot for a background solver run (M09)."""
    return _rest("POST", "rpc/planning_snapshot", json={}, token=token, timeout=30)


def record_schedule_run(payload: dict, token: str | None = None) -> WriteResult:
    """Planner/admin: store a schedule run and, when the solver produced one, a Draft version with its production
    orders and operations (M09). The database re-checks the stored operations independently."""
    result = _rest("POST", "rpc/record_schedule_run", json={"p": payload}, token=token, timeout=60)
    if result.ok:
        d = result.data or {}
        if d.get("version_number"):
            result.message = (f"Run #{d['run_number']}: {payload['status']} — saved as Draft v{d['version_number']}, "
                              f"validation {'passed' if d.get('validation_passed') else 'FAILED'}.")
        else:
            result.message = f"Run #{d.get('run_number')}: {payload['status']} — no schedule to save."
    return result


def fetch_audit(limit: int = 100, entity_type: str | None = None) -> WriteResult:
    """Audit history as the user: RLS returns rows only to approvers and admins."""
    params = {"select": "*", "order": "created_at.desc", "limit": str(limit)}
    if entity_type:
        params["entity_type"] = f"eq.{entity_type}"
    result = _rest("GET", "audit_logs", params=params, prefer="count=none")
    if result.ok and result.data is None:
        result.data = []
    return result


def insert_audit(action: str, entity: str, reason: str = "") -> None:
    """Best effort: an audit write failure must not block the user's action (M07 makes auditing transactional)."""
    _rest("POST", "audit_logs", prefer="return=minimal", json={
        "user_id": _user_id(), "action": action, "entity_type": "app", "reason": reason or None,
        "after_data": {"entity": entity}})
