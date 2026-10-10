"""Data-access layer: every page reads through these functions.

They operate on the request's dataset (app.data.dataset): the fictional in-memory `mock_data`, or a
Supabase snapshot filtered by the signed-in user's RLS (DATA_BACKEND=supabase, M06). Writes that must
persist go through app.data.writes.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app import planning
from app.data import dataset
from app.data.dataset import current as ds
from app.models import (
    AuditEntry,
    CurrentUser,
    Machine,
    WorkCenter,
    OrderStatus,
    RiskLevel,
    SalesOrder,
    ScheduledOperation,
    ScheduleVersion,
)

AUDIT_LOG: list[AuditEntry] = []

# --- Orders -------------------------------------------------------------------------

RISK_STYLE = {
    RiskLevel.HIGH: "crit",
    RiskLevel.MEDIUM: "warn",
    RiskLevel.LOW: "ok",
    RiskLevel.UNKNOWN: "muted",
}

ORDER_TABS = [
    ("all", "All Orders"),
    ("confirmed", "Confirmed"),
    ("planning", "Planning In Progress"),
    ("at_risk", "At Risk"),
    ("in_production", "In Production"),
]


def capacity_conflict(order: SalesOrder) -> bool:
    return "overload" in order.limiting_constraint.lower() or "conflict" in order.readiness.lower()


def order_view(order: SalesOrder) -> dict:
    late = planning.lateness_days(order.requested_delivery_date, order.projected_completion)
    risk = planning.risk_level(late, order.critical_shortage, order.data_complete, capacity_conflict(order))
    if late is None:
        variance = "Needs Data"
    elif late > 0:
        variance = f"+{late} Days Late"
    elif late < 0:
        variance = f"{late} Days Early"
    else:
        variance = "0 Days Slack"
    return {"o": order, "late": late, "risk": risk, "tone": RISK_STYLE[risk], "variance": variance}


def _matches_tab(view: dict, tab: str) -> bool:
    o = view["o"]
    return {
        "all": True,
        "confirmed": o.status == OrderStatus.CONFIRMED,
        "planning": o.status == OrderStatus.PLANNING,
        "at_risk": view["risk"] in (RiskLevel.HIGH, RiskLevel.UNKNOWN),
        "in_production": o.status == OrderStatus.IN_PRODUCTION,
    }.get(tab, True)


def list_orders(tab: str = "all", q: str = "", priority: str = "") -> list[dict]:
    views = [order_view(o) for o in ds().SALES_ORDERS]
    q = q.strip().lower()
    if q:
        views = [v for v in views if q in " ".join([
            v["o"].order_number, v["o"].customer_name, v["o"].spec_title, v["o"].tag,
            v["o"].customer_reference, v["o"].product_code]).lower()]
    if priority:
        views = [v for v in views if v["o"].priority.value == priority]
    views = [v for v in views if _matches_tab(v, tab)]
    # Sorted by lateness variance DESC (unknown first: it needs attention)
    return sorted(views, key=lambda v: (v["late"] is not None, -(v["late"] or 0)))


def orderable_products() -> list[dict]:
    """Active finished goods (spec §6.1: inactive products cannot be ordered), with engineering readiness."""
    rows = getattr(ds(), "PRODUCT_ROWS", None)
    if rows is None:
        candidates = [(p.product_code, p.name, p.status) for p in ds().PRODUCTS]
    else:
        candidates = [(c, p["name"], p["product_status"]) for c, p in rows.items() if p["item_type"] == "finished_good"]
    return [{"code": code, "name": name,
             "ready": code in ds().ROUTINGS and getattr(ds().BOMS.get(code), "status", "") == "Approved"}
            for code, name, status in sorted(candidates) if status == "Active"]


def next_order_number() -> str:
    numbers = [int(o.order_number.split("-")[1]) for o in ds().SALES_ORDERS
               if o.order_number.startswith("SO-") and o.order_number.split("-")[1].isdigit()]
    return f"SO-{max(numbers, default=1000) + 1}"


def order_for_edit(order_number: str) -> dict | None:
    return getattr(ds(), "ORDER_EDIT", {}).get(order_number)


def tab_counts() -> dict[str, int]:
    views = [order_view(o) for o in ds().SALES_ORDERS]
    return {key: sum(_matches_tab(v, key) for v in views) for key, _ in ORDER_TABS}


def get_order(order_number: str) -> dict | None:
    for o in ds().SALES_ORDERS:
        if o.order_number == order_number:
            return order_view(o)
    return None


def order_kpis() -> dict:
    views = [order_view(o) for o in ds().SALES_ORDERS]
    active = [v for v in views if v["o"].status not in (OrderStatus.COMPLETED, OrderStatus.CANCELLED)]
    known = [v for v in active if v["late"] is not None]
    on_time = [v for v in known if v["late"] <= 0]
    blocked = [v for v in active if v["o"].critical_shortage or not v["o"].data_complete]
    return {
        "active_count": len(active),
        "valves": sum(v["o"].quantity for v in active),
        "value": sum(v["o"].value_usd for v in active),
        "otd_pct": round(len(on_time) / len(known) * 100, 1) if known else 0.0,
        "late": len(known) - len(on_time),
        "blocked": blocked,
        "at_risk": [v for v in active if v["risk"] == RiskLevel.HIGH],
        "in_production": sum(v["o"].status == OrderStatus.IN_PRODUCTION for v in active),
    }


# --- Capacity ---------------------------------------------------------------------


def work_center_views() -> list[dict]:
    out = []
    for wc in ds().WORK_CENTERS:
        pct = planning.util_pct(wc.scheduled_hours, wc.capacity_hours)
        out.append({"wc": wc, "pct": pct, "band": planning.util_band(pct),
                    "headroom": wc.capacity_hours - wc.scheduled_hours})
    return out


def bottleneck() -> dict:
    return max(work_center_views(), key=lambda v: v["pct"])


def machine_index() -> dict[str, tuple]:
    return {m.machine_code: (wc, m) for wc in ds().WORK_CENTERS for m in wc.machines}


# --- Material ----------------------------------------------------------------------


def purchase_orders(open_only: bool = True) -> list[dict]:
    out = []
    for po in ds().PURCHASE_ORDERS:
        if open_only and po.status == "Received":
            continue
        slip = None if po.expected_delivery_date is None or po.required_date is None \
            else (po.expected_delivery_date - po.required_date).days
        out.append({"po": po, "slip": slip, "open_qty": po.ordered_quantity - po.received_quantity})
    return out


def mrp_result():
    """The MRP result for the current dataset (Supabase mode, M08); None on demo data."""
    return getattr(ds(), "MRP", None)


def shortage_skus() -> list[str]:
    """Material problems: with MRP, components short or late against their need date across all orders;
    on demo data, components short for their linked order's build quantity."""
    mrp = mrp_result()
    if mrp is not None:
        return sorted({r.item for r in mrp.problems() if r.planned_production == 0})
    skus = []
    for code, bom in ds().BOMS.items():
        if bom.status != "Approved":
            continue
        skus += [r["item"].component_code for r in bom_view(code)["shortages"]]
    return sorted(set(skus))


def alerts() -> list[dict]:
    out = []
    for p in purchase_orders():
        if p["slip"] and p["slip"] > 0:
            out.append({"severity": "crit", "type": "Late supplier receipt",
                        "text": f"{p['po'].po_number} ETA +{p['slip']}d vs need ({p['po'].linked_order})"})
        if p["po"].status == "QC Hold":
            out.append({"severity": "warn", "type": "Material on quality hold",
                        "text": f"{p['po'].po_number} {p['po'].item}: {p['po'].quality_status}"})
    b = bottleneck()
    out.append({"severity": "crit", "type": "Capacity overload",
                "text": f"{b['wc'].code} {b['wc'].name} at {b['pct']:.0f}%"})
    for v in [order_view(o) for o in ds().SALES_ORDERS]:
        if not v["o"].data_complete:
            out.append({"severity": "warn", "type": "Missing BOM / routing",
                        "text": f"{v['o'].order_number}: {v['o'].constraint_detail}"})
    return out


# --- Engineering ---------------------------------------------------------------------


def products():
    return ds().PRODUCTS


def bom_view(product_code: str, scrap_delta: float = 0.0) -> dict:
    product = next((p for p in ds().PRODUCTS if p.product_code == product_code), ds().PRODUCTS[0])
    bom = ds().BOMS.get(product.product_code)
    routing = ds().ROUTINGS.get(product.product_code)
    linked = next((o for o in ds().SALES_ORDERS if o.product_code == product.product_code
                   and o.bom_status == "Approved"), None)
    build_qty = linked.quantity if linked else 1
    rows = planning.explode_bom(bom.items, build_qty, scrap_delta) if bom else []
    return {
        "product": product,
        "bom": bom,
        "routing": routing,
        "rows": rows,
        "build_qty": build_qty,
        "linked_order": linked,
        "scrap_delta": scrap_delta,
        "unique_parts": len(rows) - 1 if rows else 0,
        "depth": (max(r["depth"] for r in rows) + 1) if rows else 0,
        "shortages": [r for r in rows if r["shortage"] > 0],
    }


def validate_bom(product_code: str) -> list[dict]:
    bom = ds().BOMS[product_code]
    checks = planning.validate_bom(bom.items, bom.status, bom_view(product_code)["build_qty"])
    checks.append({"name": "Approved routing present", "ok": product_code in ds().ROUTINGS,
                   "detail": ds().ROUTINGS[product_code].revision if product_code in ds().ROUTINGS else "Missing routing"})
    return checks


# --- Schedule / Gantt -------------------------------------------------------------------

HOUR_PX = 3
DAY_PX = 24 * HOUR_PX
GROUP_ROW_PX = 28
MACHINE_ROW_PX = 52
BAR_PX = 32


def schedule_versions() -> list[ScheduleVersion]:
    return list(ds().SCHEDULE_VERSIONS.values())


def baseline() -> ScheduleVersion:
    versions = list(ds().SCHEDULE_VERSIONS.values())
    return next((v for v in versions if v.is_baseline), versions[0])


def get_version(key: str | None) -> ScheduleVersion:
    return ds().SCHEDULE_VERSIONS.get(key or "", None) or baseline()


def _x(t: datetime) -> float:
    return planning.hours_between(ds().GANTT_START, t) * HOUR_PX


def gantt_view(version_key: str) -> dict:
    version = get_version(version_key)
    days = [ds().GANTT_START + timedelta(days=i) for i in range(ds().GANTT_DAYS)]
    rows, y = [], 0
    machine_y: dict[str, int] = {}
    for wc in ds().WORK_CENTERS:
        rows.append({"kind": "group", "wc": wc, "y": y, "h": GROUP_ROW_PX})
        y += GROUP_ROW_PX
        for m in wc.machines:
            rows.append({"kind": "machine", "wc": wc, "m": m, "y": y, "h": MACHINE_ROW_PX})
            machine_y[m.machine_code] = y
            y += MACHINE_ROW_PX
    # Hypothetical machines of a what-if scenario get their own lanes (M10)
    extra = sorted({op.machine_code for op in version.operations if op.machine_code not in machine_y})
    if extra:
        wc = WorkCenter(code="SCN", name="Scenario resources", detail="Hypothetical machines of this what-if scenario",
                        group="Scenario", group_icon="science", unit_label=f"{len(extra)} Unit{'s' if len(extra) != 1 else ''}",
                        scheduled_hours=0, capacity_hours=1, queue_note="",
                        machines=[Machine(machine_code=c, name=f"{c} (scenario)", description="Hypothetical second unit",
                                          utilization=0, down=False) for c in extra])
        rows.append({"kind": "group", "wc": wc, "y": y, "h": GROUP_ROW_PX})
        y += GROUP_ROW_PX
        for m in wc.machines:
            rows.append({"kind": "machine", "wc": wc, "m": m, "y": y, "h": MACHINE_ROW_PX})
            machine_y[m.machine_code] = y
            y += MACHINE_ROW_PX
    # Final operation of each order; flagged late when it finishes after the requested date
    last_op: dict[str, ScheduledOperation] = {}
    for op in version.operations:
        if op.machine_code not in machine_y:
            continue
        if op.order_number not in last_op or op.end > last_op[op.order_number].end:
            last_op[op.order_number] = op
    requested = {o.order_number: o.requested_delivery_date for o in ds().SALES_ORDERS}

    window_start, window_end = ds().GANTT_START, ds().GANTT_START + timedelta(days=ds().GANTT_DAYS)
    visible = [op for op in version.operations
               if op.machine_code in machine_y and op.end > window_start and op.start < window_end]

    bars, by_key = [], {}
    for op in visible:
        x0, x1 = _x(max(op.start, window_start)), _x(min(op.end, window_end))
        is_last = last_op.get(op.order_number) is op
        bar = {"op": op, "x": x0, "w": max(x1 - x0, 20), "y": machine_y[op.machine_code] + (MACHINE_ROW_PX - BAR_PX) // 2,
               "color": order_color(op.order_number),
               "late": is_last and op.order_number in requested and op.end.date() > requested[op.order_number]}
        bars.append(bar)
        by_key[f"{op.order_number}:{op.op_code}"] = bar
    blocks = [{"b": b, "x": _x(b.start), "w": _x(b.end) - _x(b.start),
               "y": machine_y[b.machine_code] + 4, "h": MACHINE_ROW_PX - 8} for b in version.blocks
              if b.machine_code in machine_y and b.end > window_start and b.start < window_end]
    markers = [{"m": mk, "x": _x(mk.at), "y": machine_y[mk.machine_code]} for mk in version.markers
               if mk.machine_code in machine_y and window_start <= mk.at < window_end]
    arrows = []
    for a, b in version.dependencies:
        if a not in by_key or b not in by_key:
            continue
        s, t = by_key[a], by_key[b]
        sx, sy = s["x"] + s["w"], s["y"] + BAR_PX / 2
        tx, ty = t["x"], t["y"] + BAR_PX / 2
        mid = max(sx + 6, min(tx - 6, sx + 10))
        arrows.append({"d": f"M{sx:.0f},{sy:.0f} H{mid:.0f} V{ty:.0f} H{tx - 2:.0f}",
                       "critical": s["op"].critical_path})
    return {
        "version": version,
        "days": days,
        "rows": rows,
        "bars": bars,
        "blocks": blocks,
        "markers": markers,
        "arrows": arrows,
        "width": ds().GANTT_DAYS * DAY_PX,
        "height": y,
        "day_px": DAY_PX,
        "today_x": _x(datetime(2025, 11, 20, 14, 32)),
    }


def active_order_legend() -> list[dict]:
    seen = []
    for op in baseline().operations:
        if op.order_number not in [s["order"] for s in seen]:
            view = get_order(op.order_number)
            if view is None:
                continue
            o = view["o"]
            seen.append({"order": op.order_number, "label": f'{o.spec_title}',
                         "color": order_color(op.order_number)})
    return seen


def scenario_comparison(cand_key: str | None = None) -> dict:
    """Baseline vs a candidate version (default: the most recent one), computed from both versions' data: planned
    completion per order, late orders, lateness, overtime and validation (M10)."""
    base = baseline()
    others = [v for v in ds().SCHEDULE_VERSIONS.values() if not v.is_baseline]
    cand = ds().SCHEDULE_VERSIONS.get(cand_key or "") if cand_key else None
    if cand is None or cand.is_baseline:
        cand = max(others, key=lambda v: v.run_number) if others else base
    changes = []
    for order in sorted(set(base.order_finish) | set(cand.order_finish)):
        b, c = base.order_finish.get(order), cand.order_finish.get(order)
        if b != c:
            changes.append({"order": order, "base": b, "cand": c, "days": (c - b).days if b and c else None})
    return {"base": base, "cand": cand, "changes": changes,
            "earlier": [x for x in changes if x["days"] is not None and x["days"] < 0],
            "later": [x for x in changes if x["days"] is not None and x["days"] > 0],
            "options": [v for v in others]}


def scenario_versions() -> list[ScheduleVersion]:
    """What-if scenario versions (run type 'simulation'), newest first (M10)."""
    return sorted((v for v in ds().SCHEDULE_VERSIONS.values() if v.run_type == "simulation"),
                  key=lambda v: v.run_number, reverse=True)


def scenario_options() -> dict:
    """Choices for the scenario builder, from the current dataset."""
    res = resources_view()
    po_lines = getattr(ds(), "PO_LINES", [])
    return {
        "po_numbers": sorted({l["po_number"] for l in po_lines if l.get("expected")}),
        "po_lines": [l for l in po_lines if l.get("expected")],
        "machines": [m["code"] for wc in res["work_centers"] for m in wc["machines"] if m["active"]],
        "work_centers": [(wc["code"], wc["name"]) for wc in res["work_centers"]],
        "orders": [o for o in ds().SALES_ORDERS
                   if o.status not in (OrderStatus.DRAFT, OrderStatus.COMPLETED, OrderStatus.CANCELLED)],
        "products": [p for p in orderable_products() if p["ready"]],
        "priorities": ["Critical", "High", "Normal", "Low"],
    }


def scenario_report(version: ScheduleVersion) -> dict | None:
    """The stored comparison of a scenario version, with plant-local times for display."""
    comp = (version.report or {}).get("comparison")
    if not comp:
        return None
    from app.data.supabase_dataset import ts

    rows = []
    for l in comp.get("delivery_changes", []):
        due = ts(l.get("due"))
        rows.append({**l, "due": due - timedelta(minutes=1) if due else None,
                     "reference_completion": ts(l.get("reference_completion")),
                     "scenario_completion": ts(l.get("scenario_completion")),
                     "approved_baseline_finish": ts(l.get("approved_baseline_finish"))})
    return {**comp, "delivery_changes": rows}


def baseline_impact() -> list[dict]:
    """Open flags raised when a supplier receipt moved after the baseline was approved (acceptance test 9)."""
    return list(getattr(ds(), "BASELINE_IMPACT", []))


def resources_view() -> dict:
    """Work centers, machines, shifts, calendar exceptions and downtime (M09). The demo data has no calendars, so
    it shows machines and the baseline's maintenance blocks read-only."""
    res = getattr(ds(), "RESOURCES", None)
    if res is not None:
        return res
    base = baseline()
    return {
        "work_centers": [{"id": wc.code, "code": wc.code, "name": wc.name, "department": wc.group,
                          "description": wc.detail, "shifts": [], "routing_ops": [],
                          "machines": [{"id": m.machine_code, "code": m.machine_code, "name": m.name, "capabilities": [],
                                        "factor": 1, "active": not m.down, "shifts": []} for m in wc.machines]}
                         for wc in ds().WORK_CENTERS],
        "downtime": [{"id": f"demo-{i}", "machine_code": b.machine_code, "start": b.start, "end": b.end,
                      "type": "breakdown" if b.kind == "downtime" else "planned_maintenance", "status": "Confirmed",
                      "reason": b.label, "by": "demo"} for i, b in enumerate(base.blocks) if b.kind in ("pm", "downtime")],
        "exceptions": [],
    }


def schedule_report(version: ScheduleVersion) -> dict | None:
    """Delivery results and the independent validation report of a solver-made version (M09), for display."""
    r = version.report
    if not r:
        return None
    from app.data.supabase_dataset import ts

    lines = []
    for l in r.get("lines", []):
        due = ts(l.get("due"))
        lines.append({**l, "due": due - timedelta(minutes=1) if due else None, "completion": ts(l.get("completion")),
                      "earliest_possible": ts(l.get("earliest_possible")), "baseline_finish": ts(l.get("baseline_finish"))})
    order = {"blocked": 0, "late": 1, "needs_data": 2, "on_time": 3}
    lines.sort(key=lambda l: (order.get(l["status"], 9), l["order_number"], l["line_number"]))
    validation = r.get("validation", {})
    return {"outcome": r.get("outcome"), "lines": lines, "validation": validation,
            "checks": [(name, validation.get("checked", {}).get(name, 0),
                        validation.get("violations_by_check", {}).get(name, 0))
                       for name in validation.get("checked", {})],
            "conflicts": r.get("conflicts", []), "db_check": r.get("db_check", []),
            "stale": r.get("validation_stale"), "issues": r.get("issues", [])}


_FALLBACK_COLORS = ["bg-primary text-on-primary", "bg-primary-fixed text-on-primary-fixed",
                    "bg-tertiary-container text-on-tertiary-container", "bg-cobalt text-white",
                    "bg-secondary-container text-on-secondary-container", "bg-surface-bright text-on-surface"]


def order_color(order_number: str) -> str:
    known = ds().ORDER_COLORS.get(order_number)
    if known:
        return known
    return _FALLBACK_COLORS[sum(map(ord, order_number)) % len(_FALLBACK_COLORS)]


# --- Approvals / audit -------------------------------------------------------------------


def approval_requests():
    return ds().APPROVAL_REQUESTS


def record_audit(action: str, entity: str, user: CurrentUser, reason: str = "", app_event: bool = False) -> AuditEntry:
    """Keep an in-process record; persist only *app events* to the database.

    Data changes are audited by Postgres triggers with full before/after rows (M07), so persisting them here would
    duplicate entries. App events — blocked attempts, solver run requests — never reach a table, so they are
    written explicitly when running on Supabase.
    """
    entry = AuditEntry(at=datetime.now(), user=user.name, action=action, entity=entity, reason=reason)
    AUDIT_LOG.append(entry)
    if app_event and dataset.is_supabase():
        from app.data import writes  # local import avoids a cycle (writes reads the dataset)
        writes.insert_audit(action, entity, reason)
    return entry


def _requested_by_user(req, user: CurrentUser) -> bool:
    if req.requested_by_id:
        return req.requested_by_id == user.id
    return req.requested_by == user.name  # mock data has names only


def request_decision(request_id: str, decision: str, user: CurrentUser, comments: str = "") -> dict:
    """Approve/reject a plan. Server-side authorization (spec §5): only an approver, never the requester.

    On Supabase the database function decide_plan_approval() performs the decision atomically (baseline switch,
    audit rows, RLS); these checks only give early, friendly messages.
    """
    req = next((r for r in ds().APPROVAL_REQUESTS if r.id == request_id), None)
    if req is None:
        return {"ok": False, "message": f"Approval request {request_id} not found."}
    if not user.can_approve:
        record_audit(f"{decision} attempt blocked", request_id, user, f"{user.role} lacks approval authority",
                     app_event=True)
        verb = {"Commit": "commit", "Approved": "approve", "Reject": "reject", "Rejected": "reject"}.get(decision, "decide")
        return {"ok": False, "message": f"{user.name} ({user.role}) cannot {verb} {req.schedule_version}. "
                                        f"Sign-off is reserved for an approver. Attempt logged to audit trail."}
    if _requested_by_user(req, user):
        record_audit(f"{decision} attempt blocked", request_id, user, "self-approval", app_event=True)
        return {"ok": False, "message": "You cannot decide on a plan you submitted yourself."}
    if req.status != "Pending":
        return {"ok": False, "message": f"{req.id} is already {req.status.lower()}."}
    new_status = "Approved" if decision in ("Commit", "Approved") else "Rejected"
    if dataset.is_supabase():
        from app.data import writes
        result = writes.decide_plan(request_id, new_status, comments)
        if not result.ok:
            return {"ok": False, "message": result.message}
        req.status = new_status
        return {"ok": True, "message": result.message}
    req.status = new_status
    record_audit(decision, request_id, user, comments)
    return {"ok": True, "message": f"{req.schedule_version} {req.status.lower()}."}


def submit_for_approval(version_key: str, notes: str, user: CurrentUser) -> dict:
    """Submit a plan for human approval. The plan is never auto-approved (spec §2 / §11.3 rule 10)."""
    version = get_version(version_key)
    if dataset.is_supabase():
        from app.data import writes
        result = writes.submit_plan(version.version_number, notes)
        if not result.ok:
            return {"error": result.message}
        entry = record_audit("Submitted for approval", f"schedule {version.key}", user, notes)
        return {"version": version, "request_number": result.data, "entry": entry, "approver": "an authorized approver"}
    req = next((r for r in ds().APPROVAL_REQUESTS if r.schedule_version == version.key), None)
    entry = record_audit("Submitted for approval", f"schedule {version.key}", user, notes)
    return {"version": version, "request": req, "request_number": req.id if req else None, "entry": entry,
            "approver": ds().APPROVER}


def withdraw_request(request_id: str, user: CurrentUser) -> dict:
    req = next((r for r in ds().APPROVAL_REQUESTS if r.id == request_id), None)
    if req is None:
        return {"ok": False, "message": f"Approval request {request_id} not found."}
    if not _requested_by_user(req, user):
        return {"ok": False, "message": "Only the requester can withdraw a request."}
    if not dataset.is_supabase():
        req.status = "Withdrawn"
        return {"ok": True, "message": f"{request_id} withdrawn (demo data)."}
    from app.data import writes
    result = writes.withdraw_plan(request_id)
    return {"ok": result.ok, "message": result.message}


def _order_finish(version: ScheduleVersion) -> dict:
    """Planned completion per order: stored per version on Supabase, derived from operations on mock data."""
    if version.order_finish:
        return dict(version.order_finish)
    finish: dict = {}
    for op in version.operations:
        d = op.end.date()
        finish[op.order_number] = max(d, finish.get(op.order_number, d))
    return finish


def version_changes(base: ScheduleVersion, cand: ScheduleVersion) -> list[dict]:
    """Orders whose planned completion differs between two versions (spec §10 "orders whose dates change")."""
    before, after = _order_finish(base), _order_finish(cand)
    requested = {o.order_number: o.requested_delivery_date for o in ds().SALES_ORDERS}
    rows = []
    for order in sorted(set(before) | set(after)):
        b, a = before.get(order), after.get(order)
        if b == a:
            continue
        req = requested.get(order)
        rows.append({
            "order": order, "before": b, "after": a, "requested": req,
            "delta_days": (a - b).days if a and b else None,
            "late_before": bool(b and req and b > req), "late_after": bool(a and req and a > req),
        })
    return rows


def approvals_overview() -> dict:
    requests = sorted(ds().APPROVAL_REQUESTS, key=lambda r: (r.status != "Pending", r.requested_at or datetime.min), )
    base = baseline()
    pending = []
    for r in requests:
        if r.status != "Pending":
            continue
        version = get_version(r.schedule_version)
        pending.append({"r": r, "version": version, "base": base, "changes": version_changes(base, version)})
    return {
        "pending": pending,
        "history": [r for r in requests if r.status != "Pending"],
        "versions": sorted(ds().SCHEDULE_VERSIONS.values(), key=lambda v: v.run_number, reverse=True),
        "baseline": base,
    }
