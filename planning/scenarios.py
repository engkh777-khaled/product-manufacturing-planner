"""What-if scenarios (spec §10).

A scenario is a list of changes applied to a *copy* of one planning snapshot — never to the database. The job then
plans twice on the same snapshot: a reference plan on today's data and the scenario plan, so every difference is
caused by the scenario. The comparison covers what spec §10 asks to show: delivery-date changes, new material
shortages, utilisation and bottlenecks, additional overtime, and the reasons for the differences.

Scenarios built on hypothetical facts (a supplier delay, an urgent order, a quantity change, an extra machine) are
*what-if only*: they are saved as separate versions but cannot be submitted for approval (D-031). Decision
scenarios (overtime, a planned machine outage, a priority change) can be.
"""

from __future__ import annotations

import copy
import math
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from planning.mrp import from_snapshot as mrp_from_snapshot
from planning.mrp import run_mrp
from planning.mrp.model import MrpResult
from planning.scheduling.inputs import baseline_horizon
from planning.scheduling.inputs import from_snapshot as sched_from_snapshot
from planning.scheduling.model import PLANT_TZ, Options, Schedule
from planning.scheduling.service import loads, payload as schedule_payload, _options_from_dict
from planning.scheduling.solver import solve
from planning.validation import ValidationReport, validate

KINDS = ("supplier_delay", "machine_unavailable", "urgent_order", "quantity_change", "overtime", "extra_machine",
         "priority")
WHAT_IF_KINDS = {"supplier_delay", "urgent_order", "quantity_change", "extra_machine"}
PRIORITIES = ("Critical", "High", "Normal", "Low")


class ScenarioError(ValueError):
    """A change that cannot be applied (unknown PO, machine, order…): shown to the planner as is."""


@dataclass
class Change:
    kind: str
    params: dict
    description: str = ""

    def as_dict(self) -> dict:
        return {"kind": self.kind, "params": self.params, "description": self.description}


@dataclass
class Applied:
    snapshot: dict
    changes: list[Change]
    affected_items: set[str] = field(default_factory=set)        # components whose supply changed
    affected_machines: set[str] = field(default_factory=set)
    affected_work_centers: set[str] = field(default_factory=set)
    affected_orders: set[str] = field(default_factory=set)

    @property
    def what_if_only(self) -> bool:
        return any(c.kind in WHAT_IF_KINDS for c in self.changes)


def _utc(local: str) -> str:
    """'2025-11-24T06:00' in plant time → UTC ISO timestamp."""
    return datetime.fromisoformat(local).replace(tzinfo=PLANT_TZ).astimezone(timezone.utc).isoformat()


def _fmt_local(local: str) -> str:
    return datetime.fromisoformat(local).strftime("%b %d %H:%M")


def apply(snap: dict, changes: list[dict | Change]) -> Applied:
    s = copy.deepcopy(snap)
    out = Applied(s, [])
    products = {p["id"]: p for p in s["products"]}
    code_to_product = {p["product_code"]: p for p in s["products"]}
    machines = {m["machine_code"]: m for m in s["machines"]}
    wcs = {w["work_center_code"]: w for w in s["work_centers"]}
    wc_code = {w["id"]: w["work_center_code"] for w in s["work_centers"]}
    orders = {o["order_number"]: o for o in s["sales_orders"]}

    for raw in changes:
        ch = raw if isinstance(raw, Change) else Change(raw["kind"], dict(raw.get("params") or {}))
        p = ch.params
        if ch.kind not in KINDS:
            raise ScenarioError(f"Unknown scenario change '{ch.kind}'")

        if ch.kind == "supplier_delay":
            po = next((h for h in s["purchase_orders"] if h["po_number"] == p.get("po_number")), None)
            if po is None:
                raise ScenarioError(f"Purchase order {p.get('po_number')} not found")
            days = int(p.get("days", 0))
            lines = [li for li in s["purchase_order_items"] if li["purchase_order_id"] == po["id"]
                     and (p.get("line_number") in (None, "") or li["line_number"] == int(p["line_number"]))
                     and li.get("expected_delivery_date")]
            if not lines or not days:
                raise ScenarioError(f"{po['po_number']}: no dated line to move, or zero days")
            moved = []
            for li in lines:
                new = date.fromisoformat(li["expected_delivery_date"][:10]) + timedelta(days=days)
                li["expected_delivery_date"] = new.isoformat()
                item = products[li["product_id"]]["product_code"]
                out.affected_items.add(item)
                moved.append(f"{po['po_number']}/{li['line_number']} ({item}) → {new:%b %d}")
            word = "delayed" if days > 0 else "earlier"
            ch.description = f"Supplier receipt {word} by {abs(days)} d: " + ", ".join(moved)

        elif ch.kind == "machine_unavailable":
            m = machines.get(p.get("machine_code"))
            if m is None:
                raise ScenarioError(f"Machine {p.get('machine_code')} not found")
            start, end = _utc(p["start"]), _utc(p["end"])
            if end <= start:
                raise ScenarioError("Machine unavailability must end after it starts")
            s["machine_downtime"].append({
                "id": str(uuid.uuid4()), "machine_id": m["id"], "start_datetime": start, "end_datetime": end,
                "downtime_type": "other", "reason": p.get("reason") or "Scenario: machine unavailable",
                "status": "Confirmed", "created_by": None, "created_at": None})
            out.affected_machines.add(m["machine_code"])
            ch.description = f"{m['machine_code']} unavailable {_fmt_local(p['start'])} – {_fmt_local(p['end'])}"

        elif ch.kind == "urgent_order":
            prod = code_to_product.get(p.get("product_code"))
            if prod is None:
                raise ScenarioError(f"Product {p.get('product_code')} not found")
            routing = next((r for r in s["routing_headers"] if r["product_id"] == prod["id"] and r["status"] == "Approved"), None)
            bom = next((b for b in s["bom_headers"] if b["product_id"] == prod["id"] and b["status"] == "Approved"), None)
            if routing is None or bom is None:
                raise ScenarioError(f"{prod['product_code']} has no approved BOM and routing — it cannot be planned")
            qty = float(p.get("quantity") or 0)
            if qty <= 0:
                raise ScenarioError("Urgent order quantity must be positive")
            priority = p.get("priority") or "Critical"
            requested = date.fromisoformat(str(p["requested_date"])[:10]).isoformat()
            number = p.get("order_number") or f"SIM-{len([o for o in orders if o.startswith('SIM-')]) + 1}"
            so_id = str(uuid.uuid4())
            s["sales_orders"].append({
                "id": so_id, "order_number": number, "customer_name": "Scenario (hypothetical)", "priority": priority,
                "requested_delivery_date": requested, "status": "Confirmed", "order_date": None, "currency": "USD"})
            s["sales_order_items"].append({
                "id": str(uuid.uuid4()), "sales_order_id": so_id, "line_number": 1, "product_id": prod["id"],
                "quantity": qty, "priority": None, "requested_delivery_date": requested, "status": "Confirmed",
                "bom_revision_id": bom["id"], "routing_revision_id": routing["id"], "unit_of_measure": "EA"})
            orders[number] = s["sales_orders"][-1]
            p["order_number"] = number
            out.affected_orders.add(number)
            ch.description = f"Urgent {priority} order {number}: {qty:g} × {prod['product_code']} by {requested}"

        elif ch.kind == "quantity_change":
            order = orders.get(p.get("order_number"))
            if order is None:
                raise ScenarioError(f"Sales order {p.get('order_number')} not found")
            pct = float(p.get("percent", 0))
            lines = [li for li in s["sales_order_items"] if li["sales_order_id"] == order["id"]
                     and (p.get("line_number") in (None, "") or li["line_number"] == int(p["line_number"]))]
            if not lines or not pct:
                raise ScenarioError(f"{order['order_number']}: no line to change, or zero percent")
            parts = []
            for li in lines:
                old = float(li["quantity"])
                new = old * (1 + pct / 100)
                if (li.get("unit_of_measure") or "EA") == "EA":
                    new = float(math.ceil(new - 1e-9))
                li["quantity"] = max(new, 1.0)
                parts.append(f"line {li['line_number']} {old:g} → {li['quantity']:g}")
            out.affected_orders.add(order["order_number"])
            ch.description = f"{order['order_number']} quantity {pct:+g}%: " + ", ".join(parts)

        elif ch.kind == "overtime":
            wc = wcs.get(p.get("work_center_code"))
            if wc is None:
                raise ScenarioError(f"Work center {p.get('work_center_code')} not found")
            start, end = _utc(p["start"]), _utc(p["end"])
            if end <= start:
                raise ScenarioError("Overtime must end after it starts")
            s.setdefault("calendar_exceptions", []).append({
                "id": str(uuid.uuid4()), "exception_type": "overtime", "description": "Scenario overtime",
                "starts_at": start, "ends_at": end, "work_center_id": wc["id"], "machine_id": None, "is_approved": True})
            out.affected_work_centers.add(wc["work_center_code"])
            ch.description = f"Overtime on {wc['work_center_code']} {_fmt_local(p['start'])} – {_fmt_local(p['end'])}"

        elif ch.kind == "extra_machine":
            m = machines.get(p.get("machine_code"))
            if m is None:
                raise ScenarioError(f"Machine {p.get('machine_code')} not found")
            new_code = p.get("new_code") or f"{m['machine_code']}-B"
            clone = {**copy.deepcopy(m), "id": str(uuid.uuid4()), "machine_code": new_code,
                     "name": f"{m['name']} (second unit, scenario)", "is_active": True}
            s["machines"].append(clone)
            for sh in [x for x in s.get("shift_calendars", []) if x.get("machine_id") == m["id"]]:
                s["shift_calendars"].append({**sh, "id": str(uuid.uuid4()), "machine_id": clone["id"]})
            machines[new_code] = clone
            out.affected_work_centers.add(wc_code[m["work_center_id"]])
            out.affected_machines.add(new_code)
            ch.description = f"Second machine {new_code} like {m['machine_code']} ({wc_code[m['work_center_id']]})"

        elif ch.kind == "priority":
            order = orders.get(p.get("order_number"))
            if order is None:
                raise ScenarioError(f"Sales order {p.get('order_number')} not found")
            if p.get("priority") not in PRIORITIES:
                raise ScenarioError(f"Priority must be one of {', '.join(PRIORITIES)}")
            old = order["priority"]
            order["priority"] = p["priority"]
            for li in s["sales_order_items"]:
                if li["sales_order_id"] == order["id"]:
                    li["priority"] = p["priority"]
            out.affected_orders.add(order["order_number"])
            ch.description = f"{order['order_number']} priority {old} → {p['priority']}"

        out.changes.append(ch)
    if not out.changes:
        raise ScenarioError("Add at least one change to the scenario")
    return out


# ------------------------------------------------------------------ comparison

def _overtime_hours(sched: Schedule) -> float:
    inp = sched.input
    total = 0.0
    for o in sched.ops:
        m = inp.machines.get(o.machine)
        for e in inp.exceptions:
            if e.kind == "overtime" and e.approved and m and e.applies_to(m):
                total += max(0.0, (min(o.end, e.end) - max(o.start, e.start)).total_seconds() / 3600)
    return round(total, 1)


def _pct(load: dict) -> float:
    return round(100 * load["scheduled_hours"] / load["capacity_hours"], 1) if load.get("capacity_hours") else 0.0


def _iso(t: datetime | None) -> str | None:
    return t.astimezone(timezone.utc).isoformat() if t else None


def compare(ref: Schedule, scen: Schedule, ref_mrp: MrpResult, scen_mrp: MrpResult, applied: Applied) -> dict:
    ref_lines = {l.key: l for l in ref.lines}
    scen_lines = {l.key: l for l in scen.lines}

    # which lines each change touches (for the "reasons" column)
    def touched_by(key: str) -> list[str]:
        order, _, line = key.partition("/")
        reasons = []
        machines_used = {o.machine for sch in (ref, scen) for o in sch.ops_for(key)}
        wcs_used = {sch.input.machines[o.machine].work_center for sch in (ref, scen) for o in sch.ops_for(key)
                    if o.machine in sch.input.machines}
        items = {r.item for r in scen_mrp.requirements if f"{r.order_number}/{r.line_number}" == key}
        for ch in applied.changes:
            if ch.kind == "supplier_delay" and items & applied.affected_items:
                reasons.append(ch.description)
            elif ch.kind == "machine_unavailable" and machines_used & applied.affected_machines:
                reasons.append(ch.description)
            elif ch.kind in ("overtime", "extra_machine") and wcs_used & applied.affected_work_centers:
                reasons.append(ch.description)
            elif ch.kind in ("quantity_change", "priority") and order in applied.affected_orders and \
                    ch.params.get("order_number") == order:
                reasons.append(ch.description)
            elif ch.kind == "urgent_order":
                urgent = ch.params["order_number"]
                urgent_machines = {o.machine for o in scen.ops if o.order_number == urgent}
                if order == urgent or machines_used & urgent_machines:
                    reasons.append(ch.description)
        return reasons

    lines = []
    for key in sorted(set(ref_lines) | set(scen_lines)):
        r, s = ref_lines.get(key), scen_lines.get(key)
        delta = None
        if r and s and r.completion and s.completion:
            delta = round((s.completion - r.completion).total_seconds() / 3600, 1)
        changed = (r is None) != (s is None) or (r and s and (r.status != s.status or (delta is not None and abs(delta) >= 1.0)))
        if not changed:
            continue
        lines.append({
            "key": key, "order_number": key.split("/")[0], "line_number": int(key.split("/")[1]),
            "priority": (s or r).priority,
            "reference_status": r.status if r else "new", "scenario_status": s.status if s else "removed",
            "reference_completion": _iso(r.completion) if r else None,
            "scenario_completion": _iso(s.completion) if s else None,
            "approved_baseline_finish": _iso((s or r).baseline_finish),
            "delta_hours": delta, "due": _iso((s or r).due),
            "reasons": touched_by(key) or ["Knock-on effect of shared capacity"],
            "limiting": s.limiting if s else [],
        })

    ref_short, scen_short = set(ref_mrp.short_items()), set(scen_mrp.short_items())
    short_req = lambda mrp: {(r.order_number, r.line_number, r.item): mrp.shortage_units(r)  # noqa: E731
                             for r in mrp.requirements if r.status == "short"}
    ref_req, scen_req = short_req(ref_mrp), short_req(scen_mrp)
    new_shortages = [{"order_number": o, "line_number": l, "item": i, "shortage": q}
                     for (o, l, i), q in sorted(scen_req.items()) if (o, l, i) not in ref_req]
    resolved = [{"order_number": o, "line_number": l, "item": i} for (o, l, i) in sorted(ref_req) if (o, l, i) not in scen_req]

    ref_wc, _ = loads(ref) if ref.ops else ({}, {})
    scen_wc, _ = loads(scen) if scen.ops else ({}, {})
    utilisation = [{"work_center": wc, "reference_pct": _pct(ref_wc.get(wc, {})), "scenario_pct": _pct(scen_wc.get(wc, {})),
                    "reference_hours": ref_wc.get(wc, {}).get("scheduled_hours", 0),
                    "scenario_hours": scen_wc.get(wc, {}).get("scheduled_hours", 0)}
                   for wc in sorted(set(ref_wc) | set(scen_wc))]
    bottleneck = lambda u, k: max(u, key=lambda x: x[k])["work_center"] if u else None  # noqa: E731

    def summary(sched: Schedule) -> dict:
        late = [l for l in sched.lines if l.status == "late"]
        return {"outcome": sched.outcome, "late_lines": len(late),
                "blocked_lines": sum(1 for l in sched.lines if l.status == "blocked"),
                "total_lateness_hours": round(sum(l.lateness_hours for l in late), 1),
                "overtime_hours": _overtime_hours(sched)}

    ref_sum, scen_sum = summary(ref), summary(scen)
    return {
        "changes": [c.as_dict() for c in applied.changes], "what_if_only": applied.what_if_only,
        "reference": ref_sum, "scenario": scen_sum,
        "additional_overtime_hours": round(scen_sum["overtime_hours"] - ref_sum["overtime_hours"], 1),
        "delivery_changes": lines, "new_shortages": new_shortages, "resolved_shortages": resolved,
        "new_short_items": sorted(scen_short - ref_short), "utilisation": utilisation,
        "bottleneck": {"reference": bottleneck(utilisation, "reference_pct"),
                       "scenario": bottleneck(utilisation, "scenario_pct")},
    }


# ------------------------------------------------------------------ one scenario run

ANCHOR_WEIGHT = 3   # per slot moved away from the reference plan (> flow weight 1: nothing moves just to finish earlier)


def _plan(snap: dict, horizon, options: Options, anchor: Schedule | None = None) -> tuple[Schedule, MrpResult]:
    """Plan one snapshot. With `anchor`, operations are pulled towards the reference plan, so the scenario plan only
    differs where the scenario forces it (otherwise two time-limited solves differ by noise)."""
    start, _ = horizon or baseline_horizon(snap)
    mrp = run_mrp(mrp_from_snapshot(snap, as_of=start))
    if anchor is not None:
        options = copy.deepcopy(options)
        options.weights.baseline_change = max(options.weights.baseline_change, ANCHOR_WEIGHT)
    inp = sched_from_snapshot(snap, horizon, options, mrp=mrp)
    if anchor is not None:
        for job in inp.jobs:
            ops = {o.op_number: (o.machine, o.start, o.end) for o in anchor.ops_for(job.key)}
            if ops:
                job.baseline = ops          # penalty anchor only; approved-baseline dates stay in baseline_finish
    return solve(inp), mrp


def run_scenario(snap: dict, changes: list[dict], name: str = "", horizon: tuple[date, date] | None = None,
                 options: Options | None = None) -> tuple[dict, Schedule, Applied]:
    """Plan today's data and the scenario on the same snapshot; return the payload for record_schedule_run()."""
    started = datetime.now(timezone.utc)
    options = options or Options()
    applied = apply(snap, changes)
    ref, ref_mrp = _plan(snap, horizon, options, anchor=None)
    scen, scen_mrp = _plan(applied.snapshot, horizon, options, anchor=ref)
    report = validate(scen) if scen.ops or scen.lines else ValidationReport()
    body = schedule_payload(scen, report, started)
    comparison = compare(ref, scen, ref_mrp, scen_mrp, applied)
    title = name.strip() or "; ".join(c.description for c in applied.changes)[:120]
    body["run_type"] = "simulation"
    body["assumptions"] = {"name": title, "changes": comparison["changes"], "what_if_only": applied.what_if_only}
    body["version"]["label"] = f"Scenario: {title}"[:200]
    body["version"]["change_summary"] = (
        f"What-if vs today's data: late lines {comparison['reference']['late_lines']} → "
        f"{comparison['scenario']['late_lines']}, blocked {comparison['reference']['blocked_lines']} → "
        f"{comparison['scenario']['blocked_lines']}, {len(comparison['delivery_changes'])} delivery changes"
        + (f", {len(comparison['new_shortages'])} new shortages" if comparison["new_shortages"] else "")
        + ("; what-if only" if applied.what_if_only else ""))
    body["version"]["metrics"]["comparison"] = comparison
    body["seconds"] = round((datetime.now(timezone.utc) - started).total_seconds(), 3)
    return body, scen, applied


def scenario_payload(snap: dict, changes: list[dict], name: str = "", horizon: tuple[str, str] | None = None,
                     options: dict | None = None) -> dict:
    """Worker-process entry point: plain JSON in, plain JSON out. ScenarioError messages are returned, not raised."""
    hz = (date.fromisoformat(horizon[0]), date.fromisoformat(horizon[1])) if horizon else None
    try:
        body, _, _ = run_scenario(snap, changes, name, hz, _options_from_dict(options))
    except ScenarioError as exc:
        return {"error": str(exc)}
    return body
