"""What-if scenarios (spec §10, §12.6, M10): build a scenario from the current plant data, plan it next to today's
data in a background job, save it as a separate version (never the baseline) and compare."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request

from app import jobs
from app.auth import require_user
from app.data import dataset
from app.data import repository as repo
from app.models import CurrentUser
from app.templating import templates
from planning.scenarios import PRIORITIES

router = APIRouter(dependencies=[Depends(require_user)])


def _can_run(user: CurrentUser) -> bool:
    return dataset.is_supabase() and bool({"planner", "admin"} & set(user.roles))


def _changes_from_form(form) -> tuple[list[dict], str | None]:
    """One optional change of each kind; a section counts when its key field is filled in."""
    f = lambda k: (form.get(k) or "").strip()  # noqa: E731
    changes = []
    try:
        if f("delay_po"):
            changes.append({"kind": "supplier_delay", "params": {"po_number": f("delay_po"), "days": int(f("delay_days") or 0)}})
        if f("down_machine"):
            changes.append({"kind": "machine_unavailable", "params": {"machine_code": f("down_machine"),
                                                                      "start": f("down_start"), "end": f("down_end")}})
        if f("urgent_product"):
            changes.append({"kind": "urgent_order", "params": {
                "product_code": f("urgent_product"), "quantity": float(f("urgent_qty") or 0),
                "requested_date": f("urgent_date"), "priority": f("urgent_priority") or "Critical"}})
        if f("qty_order"):
            changes.append({"kind": "quantity_change", "params": {"order_number": f("qty_order"),
                                                                  "percent": float(f("qty_percent") or 0)}})
        if f("ot_wc"):
            changes.append({"kind": "overtime", "params": {"work_center_code": f("ot_wc"), "start": f("ot_start"),
                                                           "end": f("ot_end")}})
        if f("extra_machine"):
            changes.append({"kind": "extra_machine", "params": {"machine_code": f("extra_machine")}})
        if f("prio_order"):
            changes.append({"kind": "priority", "params": {"order_number": f("prio_order"),
                                                           "priority": f("prio_value") or "Critical"}})
    except ValueError:
        return [], "Check the numbers in the scenario."
    for c in changes:
        for key in ("start", "end", "requested_date"):
            if key in c["params"] and not c["params"][key]:
                return [], f"Fill in the {key.replace('_', ' ')} for the {c['kind'].replace('_', ' ')} change."
    if not changes:
        return [], "Add at least one change."
    return changes, None


@router.get("/scenarios")
def scenarios_page(request: Request, user: CurrentUser = Depends(require_user)):
    return templates.TemplateResponse(request, "scenarios.html", {
        "opts": repo.scenario_options(), "scenarios": repo.scenario_versions(), "can_run": _can_run(user),
        "editable_backend": dataset.is_supabase(), "baseline": repo.baseline(), "impact": repo.baseline_impact(),
        "priorities": PRIORITIES})


@router.post("/scenarios/run")
async def run_scenario(request: Request, user: CurrentUser = Depends(require_user)):
    if not _can_run(user):
        return templates.TemplateResponse(request, "partials/scenario_run.html", {
            "job": None, "error": "Running scenarios requires DATA_BACKEND=supabase and the planner or admin role."})
    form = await request.form()
    changes, error = _changes_from_form(form)
    if error:
        return templates.TemplateResponse(request, "partials/scenario_run.html", {"job": None, "error": error})
    job = jobs.start(dataset.access_token(), user.id, user.name, kind="scenario", changes=changes,
                     name=(form.get("name") or "").strip()[:120])
    return templates.TemplateResponse(request, "partials/scenario_run.html", {"job": job})


@router.get("/scenarios/runs/{job_id}")
def scenario_status(request: Request, job_id: str, user: CurrentUser = Depends(require_user)):
    job = jobs.get(job_id, user.id)
    if job is None:
        raise HTTPException(404, "No such scenario run")
    return templates.TemplateResponse(request, "partials/scenario_run.html", {"job": job})


@router.get("/scenarios/{version_key}")
def scenario_detail(request: Request, version_key: str):
    version = repo.get_version(version_key)
    report = repo.scenario_report(version) if version.key == version_key else None
    if report is None:
        raise HTTPException(404, "No such scenario version")
    return templates.TemplateResponse(request, "scenario_detail.html", {
        "v": version, "r": report, "baseline": repo.baseline()})
