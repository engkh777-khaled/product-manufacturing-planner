"""Server-side tools for the AI planning assistant (spec §11.1, M11).

Every tool has a documented input schema (a pydantic model — also the validator), a documented output schema,
an authorization rule, clear errors, and logging. Tools read through the request's dataset, i.e. the signed-in
user's RLS snapshot, so the assistant sees exactly what the user may see.

Consequential tools (save_proposed_plan, submit_plan_for_approval) never act on their own: they register a
*pending action* that a human must confirm in the UI (spec §11.3 rule 12); confirming twice returns the first
result (idempotent). There is deliberately no tool that approves, releases production, places purchase orders or
commits a delivery date (rules 9–11).
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, ValidationError

from app.config import get_settings
from app.data import dataset, writes
from app.data import repository as repo
from app.data.dataset import current as ds
from app.models import CurrentUser

log = logging.getLogger("app.agent.tools")

ORDER = Field(..., pattern=r"^(SO|SIM)-\d{3,6}$", description="Sales order number, e.g. SO-1005")
PLANNERS = {"planner", "admin"}
RISK_MARGIN_HOURS = 48          # spec §9: less than this margin before the requested date = Medium risk


class ToolError(Exception):
    """A clear, user-facing error returned to the model as an error tool result."""


@dataclass
class ToolContext:
    user: CurrentUser
    token: str | None
    conversation_id: str = ""
    selected_order: str | None = None
    selected_version: str | None = None


@dataclass
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    output_schema: dict
    handler: Callable[[ToolContext, BaseModel], dict]
    roles: set[str] | None = None        # None = any signed-in user
    consequential: bool = False          # writes: needs explicit human confirmation

    def input_schema(self) -> dict:
        return _inline_refs(self.input_model.model_json_schema())

    def api_spec(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema()}


def _inline_refs(schema: dict) -> dict:
    """Resolve $defs/$ref so every tool schema is self-contained."""
    defs = schema.pop("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(dict(defs[node["$ref"].split("/")[-1]]))
            return {k: walk(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node
    return walk(schema)


def _iso(value) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, (date, datetime)) else str(value)


def _snapshot() -> dict:
    snap = getattr(ds(), "SNAPSHOT", None)
    if snap is None:
        raise ToolError("This tool needs live plant data (DATA_BACKEND=supabase); the demo data cannot be re-planned.")
    return snap


def _order_or_error(order_number: str):
    view = repo.get_order(order_number)
    if view is None:
        raise ToolError(f"Sales order {order_number} not found (or not visible to you).")
    return view


# ------------------------------------------------------------------ cached plan (one CP-SAT run per snapshot)

_plan_cache: dict[str, tuple[float, Any]] = {}
_plan_lock = threading.Lock()


def current_plan():
    """A fresh finite-capacity plan of the user's snapshot, cached for 5 minutes per snapshot."""
    from planning.scheduling import Options, plan
    snap = _snapshot()
    key = f"{snap.get('generated_at')}:{len(snap.get('production_operations', []))}"
    with _plan_lock:
        hit = _plan_cache.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
    result = plan(snap, None, Options(time_limit_s=get_settings().assistant_plan_seconds))
    with _plan_lock:
        if len(_plan_cache) > 8:
            _plan_cache.clear()
        _plan_cache[key] = (time.monotonic() + 300, result)
    return result


def _line_dict(line, tz) -> dict:
    local = lambda t: t.astimezone(tz).strftime("%Y-%m-%d %H:%M") if t else None  # noqa: E731
    return {"line": line.key, "item": line.item, "priority": line.priority, "status": line.status,
            "requested": (line.due.astimezone(tz) - timedelta(minutes=1)).date().isoformat() if line.status != "needs_data" else None,
            "planned_completion": local(line.completion), "lateness_hours": line.lateness_hours,
            "earliest_possible_completion": local(line.earliest_possible), "requested_date_achievable": line.achievable,
            "blocked_operations": line.blocked_ops, "limiting_factors": line.limiting,
            "approved_baseline_finish": local(line.baseline_finish)}


# ------------------------------------------------------------------ input models

class OrderIn(BaseModel):
    order_number: str = ORDER


class MaterialIn(BaseModel):
    order_number: str = ORDER
    include_covered: bool = Field(False, description="Also list requirements that are fully covered on time")


class ItemIn(BaseModel):
    item_code: str = Field(..., min_length=2, max_length=40, pattern=r"^[A-Z0-9][A-Z0-9-]*$",
                           description="Component or product code, e.g. SR-ST6-10")


class CapacityIn(BaseModel):
    work_center_code: str | None = Field(None, pattern=r"^WC-\d{2}$", description="Optional work center, e.g. WC-40")
    version: str | None = Field(None, pattern=r"^v?\d+\.\d+$", description="Schedule version for loads (default baseline)")


class ScheduleStatusIn(BaseModel):
    version: str | None = Field(None, pattern=r"^v?\d+\.\d+$", description="Version to detail (default: baseline)")


class GenerateIn(BaseModel):
    order_numbers: list[str] | None = Field(None, max_length=30, description="Only these sales orders (default: all open)")
    freeze_days: int = Field(0, ge=0, le=14, description="Keep baseline operations starting within N days fixed")


class ChangeIn(BaseModel):
    kind: Literal["supplier_delay", "machine_unavailable", "urgent_order", "quantity_change", "overtime",
                  "extra_machine", "priority"]
    params: dict = Field(..., description=(
        "supplier_delay: {po_number, days (negative = earlier)}; machine_unavailable: {machine_code, start, end} "
        "(plant-local 'YYYY-MM-DDTHH:MM'); urgent_order: {product_code, quantity, requested_date, priority}; "
        "quantity_change: {order_number, percent}; overtime: {work_center_code, start, end}; "
        "extra_machine: {machine_code}; priority: {order_number, priority}"))


class SimulateIn(BaseModel):
    changes: list[ChangeIn] = Field(..., min_length=1, max_length=7)
    name: str = Field("", max_length=120, description="Short scenario name")


class SaveIn(BaseModel):
    label: str = Field("", max_length=120, description="Why this plan is proposed (shown to the approver)")


class SubmitIn(BaseModel):
    version: str = Field(..., pattern=r"^v?\d+\.\d+$", description="Draft version to submit, e.g. 4.4")
    comments: str = Field("", max_length=1000, description="Notes for the approver")


# ------------------------------------------------------------------ handlers

def get_order_details(ctx: ToolContext, a: OrderIn) -> dict:
    view = _order_or_error(a.order_number)
    o = view["o"]
    return {"order_number": o.order_number, "customer": o.customer_name, "priority": o.priority.value,
            "status": o.status.value, "order_date": _iso(o.order_date), "requested_delivery_date": _iso(o.requested_delivery_date),
            "product": o.product_code, "description": o.spec_title, "quantity": o.quantity,
            "lines": [{"line": l.line_number, "description": l.description, "quantity": l.quantity, "note": l.note}
                      for l in o.lines],
            "bom": {"revision": o.bom_revision, "status": o.bom_status}, "routing_revision": o.routing_revision,
            "baseline_projected_completion": _iso(o.projected_completion), "days_late_vs_baseline": view["late"],
            "material_ready_date": _iso(o.material_ready), "limiting_constraint": o.limiting_constraint,
            "constraint_detail": o.constraint_detail, "risk": view["risk"].value, "data_complete": o.data_complete,
            "material_staging": [{"component": s.name, "required": s.required, "staged": s.staged, "note": s.note,
                                  "blocked_by": s.blocked_by} for s in o.staging],
            "source": f"approved baseline v{repo.baseline().version_number} + MRP on the current snapshot"}


def validate_order_readiness(ctx: ToolContext, a: OrderIn) -> dict:
    from planning.scheduling.inputs import from_snapshot
    from planning.scheduling.model import Options
    view = _order_or_error(a.order_number)
    snap = _snapshot()
    mrp = repo.mrp_result()
    inp = from_snapshot(snap, options=Options(include_drafts=True, orders=frozenset({a.order_number})), mrp=mrp)
    issues = [{"line": f"{i.order_number}/{i.line_number}", "kind": i.kind, "detail": i.detail} for i in inp.issues]
    lines = []
    for job in inp.jobs:
        blocked = job.blocked_ops()
        lines.append({"line": job.key, "item": job.item, "routing_operations": [o.number for o in job.ops],
                      "completed_operations": sorted(job.completed), "committed_operations": sorted(job.fixed),
                      "material_blocked_operations": blocked,
                      "material_gates": {op: _iso(t) for op, t in job.material_ready.items()}})
    short = [{"line": f"{r.order_number}/{r.line_number}", "item": r.item, "shortage": mrp.shortage_units(r)}
             for r in (mrp.for_order(a.order_number) if mrp else []) if r.status == "short"]
    ready = not issues and not short and all(not l["material_blocked_operations"] for l in lines)
    return {"order_number": a.order_number, "production_ready": ready, "engineering_and_resource_issues": issues,
            "material_shortages": short, "lines": lines,
            "order_status": view["o"].status.value}


def calculate_material_requirements(ctx: ToolContext, a: MaterialIn) -> dict:
    _order_or_error(a.order_number)
    mrp = repo.mrp_result()
    if mrp is None:
        raise ToolError("MRP runs on live plant data only (DATA_BACKEND=supabase).")
    rows = [r for r in mrp.for_order(a.order_number) if a.include_covered or r.status != "covered"]
    return {"order_number": a.order_number, "as_of": _iso(mrp.input.as_of), "mrp_outcome": mrp.status,
            "requirements": [{"line": f"{r.order_number}/{r.line_number}", "item": r.item, "parent": r.parent,
                              "level": r.level, "required": round(r.gross, 3), "allocated": round(r.allocated, 3),
                              "shortage": mrp.shortage_units(r) if r.status == "short" else 0,
                              "need_date": _iso(r.need_date), "operation": r.operation_ref,
                              "ready_date": _iso(r.ready_date), "status": r.status, "days_late": r.days_late,
                              "supply": [a_.ref for a_ in r.allocations],
                              "planned_production": round(r.planned_production, 3)} for r in rows],
            "issues": [vars(i) for i in mrp.issues if i.order_number == a.order_number],
            "note": "Allocation is by priority, then need date; the same stock is never allocated twice."}


def check_material_availability(ctx: ToolContext, a: ItemIn) -> dict:
    from planning.mrp.engine import projection
    mrp = repo.mrp_result()
    if mrp is None:
        raise ToolError("Material availability needs live plant data (DATA_BACKEND=supabase).")
    if a.item_code not in mrp.input.items:
        raise ToolError(f"Item {a.item_code} not found.")
    stock = [{"warehouse": r.get("warehouse_code"), "lot": r.get("lot_number"), "on_hand": r["on_hand"],
              "reserved": r["reserved"], "quality_hold": r["quality_hold"], "net_available": r["net_available"]}
             for r in getattr(ds(), "INVENTORY", []) if r["product_code"] == a.item_code]
    receipts = [{"po_line": f"{l['po_number']}/{l['line_number']}", "supplier": l["supplier_name"], "open": l["open"],
                 "expected": _iso(l["expected"]), "status": l["status"], "quality": l["quality_status"]}
                for l in getattr(ds(), "PO_LINES", []) if l["product_code"] == a.item_code]
    consumers = [{"line": f"{r.order_number}/{r.line_number}", "required": round(r.gross, 3), "need_date": _iso(r.need_date),
                  "allocated": round(r.allocated, 3), "status": r.status}
                 for r in mrp.requirements if r.item == a.item_code]
    return {"item": a.item_code, "name": mrp.input.items[a.item_code].name, "stock": stock, "open_receipts": receipts,
            "time_phased": [{**e, "date": _iso(e["date"])} for e in projection(mrp, a.item_code)],
            "demand": consumers,
            "rules": "Net available = on hand − reserved − QC hold; receipts count from their expected date; "
                     "rejected, cancelled or undated receipts do not count."}


def get_production_capacity(ctx: ToolContext, a: CapacityIn) -> dict:
    res = repo.resources_view()
    version = repo.get_version(("v" + a.version.lstrip("v")) if a.version else ctx.selected_version)
    loads = (version.report or {}).get("work_center_load") or {}
    centers = []
    for wc in res["work_centers"]:
        if a.work_center_code and wc["code"] != a.work_center_code:
            continue
        load = loads.get(wc["code"]) or {}
        pct = round(100 * load["scheduled_hours"] / load["capacity_hours"], 1) if load.get("capacity_hours") else None
        centers.append({"work_center": wc["code"], "name": wc["name"], "shifts": wc["shifts"],
                        "machines": [{"code": m["code"], "capabilities": m["capabilities"], "speed_factor": m["factor"],
                                      "active": m["active"]} for m in wc["machines"]],
                        "scheduled_hours": load.get("scheduled_hours"), "capacity_hours": load.get("capacity_hours"),
                        "load_pct": pct})
    if a.work_center_code and not centers:
        raise ToolError(f"Work center {a.work_center_code} not found.")
    downtime = [{"machine": d["machine_code"], "from": _iso(d["start"]), "to": _iso(d["end"]), "status": d["status"],
                 "reason": d["reason"]} for d in res["downtime"] if d["status"] != "Cancelled"]
    calendar = [{"kind": e["kind"], "description": e["description"], "scope": e["scope"], "from": _iso(e["start"]),
                 "to": _iso(e["end"]), "approved": e["approved"]} for e in res["exceptions"]]
    known = [c for c in centers if c["load_pct"] is not None]
    return {"version": version.version_number, "work_centers": centers, "downtime": downtime, "calendar": calendar,
            "bottleneck": max(known, key=lambda c: c["load_pct"])["work_center"] if known else None,
            "load_source": "solver output stored with the version" if loads else
                           "this version has no solver loads (seeded); use generate_production_schedule for a fresh plan"}


def get_schedule_status(ctx: ToolContext, a: ScheduleStatusIn) -> dict:
    versions = repo.schedule_versions()
    v = repo.get_version(("v" + a.version.lstrip("v")) if a.version else ctx.selected_version)
    report = repo.schedule_report(v)
    lines = [{"line": f"{l['order_number']}/{l['line_number']}", "status": l["status"],
              "planned_completion": _iso(l["completion"]), "lateness_hours": l["lateness_hours"],
              "limiting_factors": l["limiting"]} for l in (report or {}).get("lines", [])]
    return {
        "baseline": repo.baseline().version_number,
        "versions": [{"version": x.version_number, "label": x.label, "status": x.status, "is_baseline": x.is_baseline,
                      "run": x.run_number, "run_type": x.run_type, "solver_status": x.solver_status,
                      "validation_passed": x.validation_passed, "late_orders": x.late_orders,
                      "what_if_only": bool(x.assumptions.get("what_if_only"))} for x in versions],
        "detail": {"version": v.version_number, "change_summary": v.change_summary,
                   "planned_completion_by_order": {k: _iso(d) for k, d in v.order_finish.items()},
                   "lines": lines, "validation": (report or {}).get("validation", {}).get("passed")},
        "pending_approvals": [{"request": r.id, "version": r.schedule_version, "status": r.status, "requested_by": r.requested_by}
                              for r in repo.approval_requests() if r.status == "Pending"],
    }


def generate_production_schedule(ctx: ToolContext, a: GenerateIn) -> dict:
    from planning.scheduling import Options, plan
    snap = _snapshot()
    opts = Options(time_limit_s=get_settings().assistant_plan_seconds, freeze_days=a.freeze_days,
                   orders=frozenset(a.order_numbers) if a.order_numbers else None)
    sched, report = (current_plan() if not a.order_numbers and not a.freeze_days else plan(snap, None, opts))
    tz = sched.input.tz
    return {"outcome": sched.outcome, "solver_status": sched.solver_status, "solve_seconds": round(sched.seconds, 1),
            "conflicts": sched.conflicts,
            "lines": [_line_dict(l, tz) for l in sched.lines],
            "validation": {"passed": report.passed, "checks": sum(report.checked.values()),
                           "violations": [v.message for v in report.violations[:10]]},
            "saved": False,
            "note": "Not saved. To keep it as a Draft version, use save_proposed_plan (needs the user's confirmation)."}


def simulate_order_change(ctx: ToolContext, a: SimulateIn) -> dict:
    from planning.scenarios import ScenarioError, run_scenario
    from planning.scheduling import Options
    snap = _snapshot()
    try:
        body, _, _ = run_scenario(snap, [c.model_dump() for c in a.changes], a.name,
                                  options=Options(time_limit_s=get_settings().assistant_plan_seconds))
    except ScenarioError as exc:
        raise ToolError(str(exc)) from exc
    c = body["version"]["metrics"]["comparison"]
    return {"scenario": body["assumptions"]["name"], "what_if_only": c["what_if_only"], "changes": c["changes"],
            "today": c["reference"], "scenario_result": c["scenario"], "delivery_changes": c["delivery_changes"],
            "new_shortages": c["new_shortages"], "resolved_shortages": c["resolved_shortages"],
            "additional_overtime_hours": c["additional_overtime_hours"], "bottleneck": c["bottleneck"],
            "validation_passed": body["version"]["validation_passed"], "saved": False,
            "note": "Simulation only — nothing was changed or saved. Scenarios can be saved from the What-If page."}


def explain_delivery_risk(ctx: ToolContext, a: OrderIn) -> dict:
    view = _order_or_error(a.order_number)
    sched, report = current_plan()
    tz = sched.input.tz
    lines = [l for l in sched.lines if l.order_number == a.order_number]
    mrp = repo.mrp_result()
    material = [{"line": f"{r.order_number}/{r.line_number}", "item": r.item, "status": r.status,
                 "need_date": _iso(r.need_date), "ready_date": _iso(r.ready_date), "operation": r.operation_ref,
                 "shortage": mrp.shortage_units(r) if r.status == "short" else 0}
                for r in (mrp.for_order(a.order_number) if mrp else []) if r.status != "covered"]
    out = []
    for l in lines:
        if l.status == "needs_data":
            risk, why = "Unknown", "missing engineering or resource data"
        elif l.status in ("late", "blocked"):
            risk, why = "High", "expected lateness" if l.status == "late" else "unresolved material shortage"
        else:
            margin = (l.due - l.completion).total_seconds() / 3600 if l.completion else None
            risk, why = (("Medium", f"only {margin:.0f} h margin") if margin is not None and margin < RISK_MARGIN_HOURS
                         else ("Low", "feasible with margin and no critical shortage"))
        out.append({**_line_dict(l, tz), "risk": risk, "risk_rule": why})
    if not out:
        out = [{"line": f"{a.order_number}", "risk": "Unknown", "risk_rule": "order is not in the open demand that is scheduled",
                "order_status": view["o"].status.value}]
    return {"order_number": a.order_number, "lines": out, "material_problems": material,
            "plan": {"outcome": sched.outcome, "validation_passed": report.passed, "basis": "fresh CP-SAT plan of the current snapshot"},
            "risk_thresholds": {"medium_if_margin_below_hours": RISK_MARGIN_HOURS},
            "note": "Risk levels follow explicit rules (spec §9); they are not statistical probabilities."}


# ------------------------------------------------------------------ consequential tools → pending actions

@dataclass
class PendingAction:
    id: str
    user_id: str
    conversation_id: str
    tool: str
    args: dict
    summary: str
    created_at: datetime
    status: str = "pending"            # pending | done | failed | dismissed
    result: str = ""


_pending: dict[str, PendingAction] = {}
_pending_lock = threading.Lock()


def _propose(ctx: ToolContext, tool: str, args: dict, summary: str) -> dict:
    with _pending_lock:
        same = next((p for p in _pending.values() if p.user_id == ctx.user.id and p.tool == tool and p.args == args
                     and p.status == "pending"), None)       # idempotent: the same proposal is not duplicated
        action = same or PendingAction(uuid.uuid4().hex[:12], ctx.user.id, ctx.conversation_id, tool, args, summary,
                                       datetime.now(timezone.utc))
        _pending[action.id] = action
    log.info("assistant proposed %s for %s: %s", tool, ctx.user.email or ctx.user.id, summary)
    return {"status": "confirmation_required", "action_id": action.id, "summary": summary,
            "message": "Nothing has been done yet. The user must press Confirm in the assistant panel."}


def save_proposed_plan(ctx: ToolContext, a: SaveIn) -> dict:
    _snapshot()
    return _propose(ctx, "save_proposed_plan", a.model_dump(),
                    "Run the CP-SAT solver on the current data and save the result as a Draft schedule version"
                    + (f" ({a.label})" if a.label else "") + ". It is not approved and does not change the baseline.")


def submit_plan_for_approval(ctx: ToolContext, a: SubmitIn) -> dict:
    number = a.version.lstrip("v")
    v = repo.get_version(f"v{number}")
    if v.version_number != number:
        raise ToolError(f"Version {number} not found.")
    if v.is_baseline or v.status not in ("Draft",):
        raise ToolError(f"Version {number} is {v.status}{' (baseline)' if v.is_baseline else ''}; only Draft versions can be submitted.")
    if v.assumptions.get("what_if_only"):
        raise ToolError(f"Version {number} is a what-if scenario and can never be submitted for approval.")
    if not v.validation_passed:
        raise ToolError(f"Version {number} has not passed independent validation.")
    return _propose(ctx, "submit_plan_for_approval", {"version": number, "comments": a.comments},
                    f"Submit Draft v{number} for approval by an authorized approver. The assistant cannot approve it.")


def pending_for(user_id: str, conversation_id: str | None = None) -> list[PendingAction]:
    return [p for p in _pending.values() if p.user_id == user_id and p.status == "pending"
            and (conversation_id is None or p.conversation_id == conversation_id)]


def execute_action(action_id: str, user: CurrentUser) -> PendingAction:
    """Run a confirmed action as the human user (their token, their RLS). Idempotent."""
    with _pending_lock:
        action = _pending.get(action_id)
        if action is None or action.user_id != user.id:
            raise ToolError("No such pending action.")
        if action.status != "pending":
            return action                                   # confirmed before: same result, nothing re-run
        action.status = "running"
    try:
        if action.tool == "save_proposed_plan":
            from planning.scheduling import plan_payload
            body = plan_payload(_snapshot(), None, {"time_limit_s": get_settings().scheduler_time_limit_seconds})
            if action.args.get("label"):
                body["version"]["label"] = f"Assistant proposal — {action.args['label']}"[:200]
            result = writes.record_schedule_run(body)
        elif action.tool == "submit_plan_for_approval":
            result = writes.submit_plan(action.args["version"], action.args.get("comments") or "")
        else:
            raise ToolError(f"Unknown action {action.tool}")
        action.status, action.result = ("done" if result.ok else "failed"), result.message
        if result.ok:
            repo.record_audit(f"Assistant action confirmed: {action.tool}", action.summary[:120], user,
                              result.message, app_event=True)
    except Exception as exc:  # report, never crash
        log.exception("assistant action %s failed", action.id)
        action.status, action.result = "failed", f"Failed: {exc}"
    log.info("assistant action %s %s by %s: %s", action.tool, action.status, user.email or user.id, action.result)
    return action


def dismiss_action(action_id: str, user: CurrentUser) -> None:
    with _pending_lock:
        action = _pending.get(action_id)
        if action and action.user_id == user.id and action.status == "pending":
            action.status = "dismissed"


# ------------------------------------------------------------------ registry

def _obj(**props) -> dict:
    return {"type": "object", "properties": props}


TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("get_order_details", "Sales order header and lines, BOM/routing revision status, the approved baseline's "
         "projected completion, material staging and the limiting constraint.", OrderIn,
         _obj(order_number={"type": "string"}, status={"type": "string"}, requested_delivery_date={"type": "string"},
              lines={"type": "array"}, baseline_projected_completion={"type": ["string", "null"]},
              limiting_constraint={"type": "string"}, risk={"type": "string"}, material_staging={"type": "array"}),
         get_order_details),
    Tool("validate_order_readiness", "Is the order production-ready? Checks approved BOM and routing, qualified "
         "machines, shift calendars, completed/committed operations and material gates per line.", OrderIn,
         _obj(production_ready={"type": "boolean"}, engineering_and_resource_issues={"type": "array"},
              material_shortages={"type": "array"}, lines={"type": "array"}),
         validate_order_readiness),
    Tool("calculate_material_requirements", "Multilevel MRP for one order: requirements per component with need date, "
         "consuming operation, allocated supply, shortage and ready date.", MaterialIn,
         _obj(mrp_outcome={"type": "string"}, requirements={"type": "array"}, issues={"type": "array"}),
         calculate_material_requirements),
    Tool("check_material_availability", "Stock, QC holds, open receipts, time-phased availability and competing demand "
         "for one item.", ItemIn,
         _obj(stock={"type": "array"}, open_receipts={"type": "array"}, time_phased={"type": "array"},
              demand={"type": "array"}),
         check_material_availability),
    Tool("get_production_capacity", "Work centers, machines and capabilities, shifts, downtime, calendar exceptions, "
         "scheduled vs available hours of a version and the bottleneck.", CapacityIn,
         _obj(work_centers={"type": "array"}, downtime={"type": "array"}, calendar={"type": "array"},
              bottleneck={"type": ["string", "null"]}),
         get_production_capacity),
    Tool("get_schedule_status", "Schedule versions (baseline, drafts, scenarios) with solver status and validation, "
         "and the delivery lines of one version.", ScheduleStatusIn,
         _obj(baseline={"type": "string"}, versions={"type": "array"}, detail={"type": "object"},
              pending_approvals={"type": "array"}),
         get_schedule_status),
    Tool("generate_production_schedule", "Run the finite-capacity CP-SAT scheduler on the current data and the "
         "independent validator. Read-only: nothing is saved. Returns outcome, per-line completion, lateness, "
         "earliest possible completion and limiting factors.", GenerateIn,
         _obj(outcome={"type": "string"}, lines={"type": "array"}, validation={"type": "object"}, saved={"type": "boolean"}),
         generate_production_schedule, roles=PLANNERS),
    Tool("simulate_order_change", "What-if simulation (spec §10): applies changes to a copy of the data and compares "
         "with today's plan — delivery changes with reasons, shortages, utilisation, overtime. Nothing is saved.",
         SimulateIn,
         _obj(delivery_changes={"type": "array"}, new_shortages={"type": "array"}, today={"type": "object"},
              scenario_result={"type": "object"}, saved={"type": "boolean"}),
         simulate_order_change, roles=PLANNERS),
    Tool("explain_delivery_risk", "Risk level (Low/Medium/High/Unknown, explicit rules) per order line from a fresh "
         "plan, with lateness, earliest possible completion, limiting factors and material problems.", OrderIn,
         _obj(lines={"type": "array"}, material_problems={"type": "array"}, plan={"type": "object"}),
         explain_delivery_risk),
    Tool("save_proposed_plan", "Propose saving a freshly solved plan as a Draft version. Does NOT save: it asks the "
         "user to confirm in the UI. Never approves.", SaveIn,
         _obj(status={"const": "confirmation_required"}, action_id={"type": "string"}, summary={"type": "string"}),
         save_proposed_plan, roles=PLANNERS, consequential=True),
    Tool("submit_plan_for_approval", "Propose submitting a validated Draft version to an authorized approver. Does NOT "
         "submit: the user must confirm in the UI. The assistant can never approve a plan.", SubmitIn,
         _obj(status={"const": "confirmation_required"}, action_id={"type": "string"}, summary={"type": "string"}),
         submit_plan_for_approval, roles=PLANNERS, consequential=True),
]}

FORBIDDEN_VERBS = ("approve", "release", "purchase", "place_po", "commit", "delete")   # tested: no such tools exist


def run_tool(ctx: ToolContext, name: str, raw_input: Any) -> tuple[dict, bool]:
    """Validate, authorize and execute one tool call. Returns (result, is_error)."""
    started = time.perf_counter()
    tool = TOOLS.get(name)
    if tool is None:
        return {"error": f"Unknown tool '{name}'. Available: {', '.join(TOOLS)}"}, True
    if tool.roles is not None and not (tool.roles & set(ctx.user.roles)):
        log.warning("assistant tool %s denied for %s (roles %s)", name, ctx.user.email or ctx.user.id, ctx.user.roles)
        return {"error": f"Not authorized: {name} requires one of the roles {sorted(tool.roles)}; "
                         f"the user has {sorted(ctx.user.roles) or 'none'}."}, True
    try:
        args = tool.input_model.model_validate(raw_input or {})
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'input'}: {e['msg']}" for e in exc.errors()[:5])
        return {"error": f"Invalid input for {name}: {problems}"}, True
    try:
        result = tool.handler(ctx, args)
        is_error = False
    except ToolError as exc:
        result, is_error = {"error": str(exc)}, True
    except Exception as exc:  # never leak a traceback to the model or the user
        log.exception("assistant tool %s failed", name)
        result, is_error = {"error": f"{name} failed unexpectedly ({type(exc).__name__}). Report the failure; do not guess."}, True
    log.info("assistant tool %s by %s in %.2fs%s", name, ctx.user.email or ctx.user.id,
             time.perf_counter() - started, " (error)" if is_error else "")
    return result, is_error
