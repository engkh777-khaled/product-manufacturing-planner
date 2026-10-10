"""Resources & Calendars (spec §12.5, M09): work centers, machines, capabilities, routing assignments, shifts,
holidays, downtime and approved overtime — the scheduler's capacity inputs. Supervisors and admins record downtime
and calendar exceptions (RLS enforces the same roles)."""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse

from app.auth import require_user
from app.data import dataset, writes
from app.data import repository as repo
from app.models import CurrentUser
from app.templating import templates

router = APIRouter(dependencies=[Depends(require_user)])
PLANT_TZ = ZoneInfo("America/Chicago")
DOWNTIME_TYPES = ("planned_maintenance", "breakdown", "other")
DOWNTIME_STATUSES = ("Planned", "Confirmed", "Cancelled")
EXCEPTION_TYPES = ("holiday", "shutdown", "overtime")


def _can_edit(user: CurrentUser) -> bool:
    return dataset.is_supabase() and bool({"supervisor", "admin"} & set(user.roles))


def _back(msg: str) -> RedirectResponse:
    return RedirectResponse(f"/resources?msg={quote(msg[:300])}", 303)


def _utc(local: str) -> str:
    """<input type=datetime-local> value in plant time → UTC ISO timestamp."""
    return datetime.fromisoformat(local).replace(tzinfo=PLANT_TZ).astimezone(timezone.utc).isoformat()


@router.get("/resources")
def resources_page(request: Request, msg: str = "", user: CurrentUser = Depends(require_user)):
    return templates.TemplateResponse(request, "resources.html", {
        "res": repo.resources_view(), "can_edit": _can_edit(user), "msg": msg[:300],
        "editable_backend": dataset.is_supabase(), "downtime_types": DOWNTIME_TYPES,
        "downtime_statuses": DOWNTIME_STATUSES, "exception_types": EXCEPTION_TYPES})


@router.post("/resources/downtime")
def add_downtime(machine_code: str = Form(...), start: str = Form(...), end: str = Form(...),
                 downtime_type: str = Form("planned_maintenance"), status: str = Form("Planned"),
                 reason: str = Form(...), user: CurrentUser = Depends(require_user)):
    if not _can_edit(user):
        return _back("Recording downtime requires DATA_BACKEND=supabase and the supervisor or admin role.")
    machine = next((m for wc in repo.resources_view()["work_centers"] for m in wc["machines"] if m["code"] == machine_code), None)
    try:
        start_utc, end_utc = _utc(start), _utc(end)
    except ValueError:
        return _back("Enter a valid start and end.")
    if machine is None or downtime_type not in DOWNTIME_TYPES or status not in DOWNTIME_STATUSES or not reason.strip():
        return _back("Choose a machine, type, status and give a reason.")
    if end_utc <= start_utc:
        return _back("Downtime must end after it starts.")
    result = writes.create_downtime({"machine_id": machine["id"], "start_datetime": start_utc, "end_datetime": end_utc,
                                     "downtime_type": downtime_type, "status": status, "reason": reason.strip()[:200]},
                                    machine_code)
    if result.ok:
        repo.record_audit("Downtime recorded", f"{machine_code} {start}–{end}", user, f"{status}: {reason.strip()}")
    return _back(result.message + (" Re-run the solver to plan around it." if result.ok and status == "Confirmed" else ""))


@router.post("/resources/downtime/{downtime_id}/status")
def downtime_status(downtime_id: str, status: str = Form(...), user: CurrentUser = Depends(require_user)):
    if not _can_edit(user) or status not in DOWNTIME_STATUSES:
        return _back("Changing downtime requires the supervisor or admin role.")
    return _back(writes.set_downtime_status(downtime_id, status).message)


@router.post("/resources/exceptions")
def add_exception(exception_type: str = Form(...), description: str = Form(...), start: str = Form(...),
                  end: str = Form(...), scope: str = Form(""), approved: bool = Form(False),
                  user: CurrentUser = Depends(require_user)):
    if not _can_edit(user):
        return _back("Calendar changes require DATA_BACKEND=supabase and the supervisor or admin role.")
    if exception_type not in EXCEPTION_TYPES or not description.strip():
        return _back("Choose a type and give a description.")
    try:
        start_utc, end_utc = _utc(start), _utc(end)
    except ValueError:
        return _back("Enter a valid start and end.")
    if end_utc <= start_utc:
        return _back("The calendar entry must end after it starts.")
    res = repo.resources_view()
    values = {"exception_type": exception_type, "description": description.strip()[:200], "starts_at": start_utc,
              "ends_at": end_utc, "is_approved": approved or exception_type != "overtime"}
    if scope:
        wc = next((w for w in res["work_centers"] if w["code"] == scope), None)
        machine = next((m for w in res["work_centers"] for m in w["machines"] if m["code"] == scope), None)
        if wc:
            values["work_center_id"] = wc["id"]
        elif machine:
            values["machine_id"] = machine["id"]
        else:
            return _back(f"Unknown work center or machine {scope}.")
    return _back(writes.create_calendar_exception(values).message)


@router.post("/resources/exceptions/{exception_id}/approval")
def exception_approval(exception_id: str, approved: bool = Form(...), user: CurrentUser = Depends(require_user)):
    if not _can_edit(user):
        return _back("Approving overtime requires the supervisor or admin role.")
    return _back(writes.set_exception_approval(exception_id, approved).message)
