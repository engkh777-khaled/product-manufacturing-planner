"""Data-access layer. The single swap point for Supabase / the Python planning API.

Every page reads through these functions; today they serve the fictional in-memory
dataset in `mock_data`. Write actions (approval submissions) are kept in process
memory and recorded in the audit log.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from app import planning
from app.data import mock_data as db
from app.models import (
    AuditEntry,
    CurrentUser,
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
    views = [order_view(o) for o in db.SALES_ORDERS]
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


def tab_counts() -> dict[str, int]:
    views = [order_view(o) for o in db.SALES_ORDERS]
    return {key: sum(_matches_tab(v, key) for v in views) for key, _ in ORDER_TABS}


def get_order(order_number: str) -> dict | None:
    for o in db.SALES_ORDERS:
        if o.order_number == order_number:
            return order_view(o)
    return None


def order_kpis() -> dict:
    views = [order_view(o) for o in db.SALES_ORDERS]
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
    for wc in db.WORK_CENTERS:
        pct = planning.util_pct(wc.scheduled_hours, wc.capacity_hours)
        out.append({"wc": wc, "pct": pct, "band": planning.util_band(pct),
                    "headroom": wc.capacity_hours - wc.scheduled_hours})
    return out


def bottleneck() -> dict:
    return max(work_center_views(), key=lambda v: v["pct"])


def machine_index() -> dict[str, tuple]:
    return {m.machine_code: (wc, m) for wc in db.WORK_CENTERS for m in wc.machines}


# --- Material ----------------------------------------------------------------------


def purchase_orders(open_only: bool = True) -> list[dict]:
    out = []
    for po in db.PURCHASE_ORDERS:
        if open_only and po.status == "Received":
            continue
        slip = None if po.expected_delivery_date is None else (po.expected_delivery_date - po.required_date).days
        out.append({"po": po, "slip": slip, "open_qty": po.ordered_quantity - po.received_quantity})
    return out


def shortage_skus() -> list[str]:
    """Components short for their linked order's build quantity (approved BOMs only)."""
    skus = []
    for code, bom in db.BOMS.items():
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
    for v in [order_view(o) for o in db.SALES_ORDERS]:
        if not v["o"].data_complete:
            out.append({"severity": "warn", "type": "Missing BOM / routing",
                        "text": f"{v['o'].order_number}: {v['o'].constraint_detail}"})
    return out


# --- Engineering ---------------------------------------------------------------------


def products():
    return db.PRODUCTS


def bom_view(product_code: str, scrap_delta: float = 0.0) -> dict:
    product = next((p for p in db.PRODUCTS if p.product_code == product_code), db.PRODUCTS[0])
    bom = db.BOMS.get(product.product_code)
    routing = db.ROUTINGS.get(product.product_code)
    linked = next((o for o in db.SALES_ORDERS if o.product_code == product.product_code
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
    bom = db.BOMS[product_code]
    checks = planning.validate_bom(bom.items, bom.status, bom_view(product_code)["build_qty"])
    checks.append({"name": "Approved routing present", "ok": product_code in db.ROUTINGS,
                   "detail": db.ROUTINGS[product_code].revision if product_code in db.ROUTINGS else "Missing routing"})
    return checks


# --- Schedule / Gantt -------------------------------------------------------------------

HOUR_PX = 3
DAY_PX = 24 * HOUR_PX
GROUP_ROW_PX = 28
MACHINE_ROW_PX = 52
BAR_PX = 32


def schedule_versions() -> list[ScheduleVersion]:
    return list(db.SCHEDULE_VERSIONS.values())


def get_version(key: str) -> ScheduleVersion:
    return db.SCHEDULE_VERSIONS.get(key, db.SCHEDULE_VERSIONS["v4.2"])


def _x(t: datetime) -> float:
    return planning.hours_between(db.GANTT_START, t) * HOUR_PX


def gantt_view(version_key: str) -> dict:
    version = get_version(version_key)
    days = [db.GANTT_START + timedelta(days=i) for i in range(db.GANTT_DAYS)]
    rows, y = [], 0
    machine_y: dict[str, int] = {}
    for wc in db.WORK_CENTERS:
        rows.append({"kind": "group", "wc": wc, "y": y, "h": GROUP_ROW_PX})
        y += GROUP_ROW_PX
        for m in wc.machines:
            rows.append({"kind": "machine", "wc": wc, "m": m, "y": y, "h": MACHINE_ROW_PX})
            machine_y[m.machine_code] = y
            y += MACHINE_ROW_PX
    # Final operation of each order; flagged late when it finishes after the requested date
    last_op: dict[str, ScheduledOperation] = {}
    for op in version.operations:
        if op.order_number not in last_op or op.end > last_op[op.order_number].end:
            last_op[op.order_number] = op
    requested = {o.order_number: o.requested_delivery_date for o in db.SALES_ORDERS}

    bars, by_key = [], {}
    for op in version.operations:
        x0, x1 = _x(op.start), _x(op.end)
        is_last = last_op[op.order_number] is op
        bar = {"op": op, "x": x0, "w": max(x1 - x0, 20), "y": machine_y[op.machine_code] + (MACHINE_ROW_PX - BAR_PX) // 2,
               "color": db.ORDER_COLORS.get(op.order_number, "bg-primary text-on-primary"),
               "late": is_last and op.end.date() > requested[op.order_number]}
        bars.append(bar)
        by_key[f"{op.order_number}:{op.op_code}"] = bar
    blocks = [{"b": b, "x": _x(b.start), "w": _x(b.end) - _x(b.start),
               "y": machine_y[b.machine_code] + 4, "h": MACHINE_ROW_PX - 8} for b in version.blocks]
    markers = [{"m": mk, "x": _x(mk.at), "y": machine_y[mk.machine_code]} for mk in version.markers]
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
        "width": db.GANTT_DAYS * DAY_PX,
        "height": y,
        "day_px": DAY_PX,
        "today_x": _x(datetime(2025, 11, 20, 14, 32)),
    }


def active_order_legend() -> list[dict]:
    seen = []
    for op in get_version("v4.2").operations:
        if op.order_number not in [s["order"] for s in seen]:
            o = get_order(op.order_number)["o"]
            seen.append({"order": op.order_number, "label": f'{o.spec_title}',
                         "color": db.ORDER_COLORS.get(op.order_number, "bg-primary")})
    return seen


def scenario_comparison() -> dict:
    base, cand = get_version("v4.2"), get_version("v4.3")
    return {"base": base, "cand": cand}


# --- Approvals / audit -------------------------------------------------------------------


def approval_requests():
    return db.APPROVAL_REQUESTS


def record_audit(action: str, entity: str, user: CurrentUser, reason: str = "") -> AuditEntry:
    entry = AuditEntry(at=datetime.now(), user=user.name, action=action, entity=entity, reason=reason)
    AUDIT_LOG.append(entry)
    return entry


def request_decision(request_id: str, decision: str, user: CurrentUser) -> dict:
    """Approve/reject a plan. Server-side authorization (spec §5): only an approver, never the requester."""
    req = next((r for r in db.APPROVAL_REQUESTS if r.id == request_id), None)
    if req is None:
        return {"ok": False, "message": f"Approval request {request_id} not found."}
    if not user.can_approve:
        record_audit(f"{decision} attempt blocked", request_id, user, f"{user.role} lacks approval authority")
        return {"ok": False, "message": f"{user.name} ({user.role}) cannot {decision.lower()} {req.schedule_version}. "
                                        f"Sign-off is reserved for an approver. Attempt logged to audit trail."}
    if req.requested_by == user.name:
        record_audit(f"{decision} attempt blocked", request_id, user, "self-approval")
        return {"ok": False, "message": "You cannot decide on a plan you submitted yourself."}
    if req.status != "Pending":
        return {"ok": False, "message": f"{req.id} is already {req.status.lower()}."}
    req.status = "Approved" if decision == "Commit" else "Rejected"
    record_audit(decision, request_id, user)
    return {"ok": True, "message": f"{req.schedule_version} {req.status.lower()}."}


def submit_for_approval(version_key: str, notes: str, user: CurrentUser) -> dict:
    """Records a submission. The plan is never auto-approved (spec §2 / §11.3 rule 10)."""
    version = get_version(version_key)
    req = next((r for r in db.APPROVAL_REQUESTS if r.schedule_version == version.key), None)
    entry = record_audit("Submitted for approval", f"schedule {version.key}", user, notes)
    return {"version": version, "request": req, "entry": entry, "approver": db.APPROVER}
