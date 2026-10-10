"""Build scheduling inputs from one `public.planning_snapshot()` document (spec §4, §8.1).

- Demand: open sales order lines (Confirmed, Planning, Planned, In Production; drafts only on request — D-028).
- Routing: the line's routing revision when approved, else the product's approved routing.
- Material: MRP (M08) on the same snapshot. Each level-1 requirement gates its consuming operation (BOM
  operation reference, else the first operation): from its ready date, or never if it is short.
- Commitments from the approved baseline: completed operations are not rescheduled; released, in-progress and
  frozen operations (plus, optionally, everything inside the freeze window) are fixed.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from planning.mrp import from_snapshot as mrp_from_snapshot
from planning.mrp import run_mrp
from planning.mrp.model import MrpResult
from planning.scheduling.model import (PLANT_TZ, CalendarException, DataIssue, Downtime, FixedOp, Job, Machine,
                                       Options, RoutingOp, SchedulingInput, Shift)

SCHEDULED_STATUSES = {"Confirmed", "Planning", "Planned", "In Production"}
FIXED_OP_STATUSES = {"Released", "In Progress"}


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value).astimezone(timezone.utc) if value else None


def _day(value: str | None) -> date | None:
    return date.fromisoformat(value[:10]) if value else None


def _time(value: str) -> time:
    return time.fromisoformat(value)


def local_midnight(d: date) -> datetime:
    return datetime.combine(d, time(0), PLANT_TZ).astimezone(timezone.utc)


def production_order_number(order_number: str, line_number: int) -> str:
    return f"PRD-{order_number.removeprefix('SO-')}-{line_number}"


def baseline_horizon(snap: dict) -> tuple[date, date]:
    baseline = next((v for v in snap["schedule_versions"] if v["is_baseline"]), None)
    run = next((r for r in snap["planning_runs"] if baseline and r["id"] == baseline["planning_run_id"]), None)
    if run:
        return _day(run["planning_horizon_start"]), _day(run["planning_horizon_end"])
    today = date.today()
    return today, today + timedelta(days=28)


def from_snapshot(snap: dict, horizon: tuple[date, date] | None = None, options: Options | None = None,
                  mrp: MrpResult | None = None) -> SchedulingInput:
    options = options or Options()
    start_day, end_day = horizon or baseline_horizon(snap)
    as_of = local_midnight(start_day)
    mrp = mrp or run_mrp(mrp_from_snapshot(snap, as_of=start_day))

    wc_code = {w["id"]: w["work_center_code"] for w in snap["work_centers"]}
    machines = {m["machine_code"]: Machine(code=m["machine_code"], work_center=wc_code[m["work_center_id"]],
                                           capabilities=frozenset(m.get("capability_tags") or []),
                                           factor=float(m.get("capacity_factor") or 1), active=m["is_active"],
                                           name=m["name"])
                for m in snap["machines"]}
    machine_code = {m["id"]: m["machine_code"] for m in snap["machines"]}

    wc_shifts, machine_shifts = defaultdict(list), defaultdict(list)
    for s in snap.get("shift_calendars", []):
        shift = Shift(start=_time(s["start_time"]), end=_time(s["end_time"]), days=frozenset(s["working_days"]),
                      valid_from=_day(s.get("valid_from")), valid_to=_day(s.get("valid_to")), name=s["shift_name"])
        if s.get("machine_id"):
            machine_shifts[machine_code[s["machine_id"]]].append(shift)
        else:
            wc_shifts[wc_code[s["work_center_id"]]].append(shift)
    exceptions = [CalendarException(kind=e["exception_type"], start=_ts(e["starts_at"]), end=_ts(e["ends_at"]),
                                    work_center=wc_code.get(e.get("work_center_id")),
                                    machine=machine_code.get(e.get("machine_id")), approved=e["is_approved"],
                                    description=e["description"])
                  for e in snap.get("calendar_exceptions", [])]
    downtime = [Downtime(machine=machine_code[d["machine_id"]], start=_ts(d["start_datetime"]), end=_ts(d["end_datetime"]),
                         status=d["status"], reason=d["reason"]) for d in snap["machine_downtime"]]

    products = {p["id"]: p for p in snap["products"]}
    routing_headers = {r["id"]: r for r in snap["routing_headers"]}
    approved_routing = {r["product_id"]: r["id"] for r in snap["routing_headers"] if r["status"] == "Approved"}
    rops_by_header = defaultdict(list)
    for o in snap["routing_operations"]:
        rops_by_header[o["routing_header_id"]].append(o)
    rop_by_id = {o["id"]: o for o in snap["routing_operations"]}
    preds = defaultdict(list)
    for d in snap["routing_dependencies"]:
        preds[d["operation_id"]].append(rop_by_id[d["predecessor_id"]]["operation_number"])

    def routing_ops(header_id: str) -> list[RoutingOp]:
        rows = sorted(rops_by_header[header_id], key=lambda o: o["sequence"])
        explicit = any(preds.get(o["id"]) for o in rows)
        ops = []
        for i, o in enumerate(rows):
            if explicit:
                pre = tuple(preds.get(o["id"], []))
            else:
                pre = (rows[i - 1]["operation_number"],) if i else ()   # no explicit links: follow the sequence
            ops.append(RoutingOp(number=o["operation_number"], name=o["operation_name"],
                                 work_center=wc_code[o["work_center_id"]], setup_min=float(o["setup_time_minutes"]),
                                 cycle_min=float(o["cycle_time_minutes"]),
                                 inspection_min=float(o["inspection_time_minutes"]), basis=o["time_basis"],
                                 max_batch=o.get("maximum_batch_quantity"),
                                 capabilities=frozenset(o.get("required_capabilities") or []),
                                 predecessors=pre, quality_gate=o["is_quality_gate"], routing_op_id=o["id"]))
        return ops

    # Baseline commitments per sales order line.
    baseline = next((v for v in snap["schedule_versions"] if v["is_baseline"]), None)
    base_pos = {p["id"]: p for p in snap["production_orders"] if baseline and p["schedule_version_id"] == baseline["id"]}
    base_ops = defaultdict(list)
    for op in snap["production_operations"]:
        if op["production_order_id"] in base_pos:
            base_ops[base_pos[op["production_order_id"]].get("sales_order_item_id")].append(op)
    base_finish = {p.get("sales_order_item_id"): _ts(p.get("planned_finish")) for p in base_pos.values()}
    freeze_until = as_of + timedelta(days=options.freeze_days)

    missing_bom = {(i.order_number, i.line_number) for i in mrp.issues if i.kind == "missing_bom"}
    orders = {o["id"]: o for o in snap["sales_orders"]}
    included = SCHEDULED_STATUSES | ({"Draft"} if options.include_drafts else set())
    jobs, issues, notes = [], [], []
    for li in sorted(snap["sales_order_items"], key=lambda x: (orders[x["sales_order_id"]]["order_number"], x["line_number"])):
        order = orders[li["sales_order_id"]]
        so, ln = order["order_number"], li["line_number"]
        if order["status"] not in included or li["status"] not in included:
            continue
        if options.orders is not None and so not in options.orders:
            continue
        product = products[li["product_id"]]
        header_id = li.get("routing_revision_id")
        if not header_id or routing_headers.get(header_id, {}).get("status") != "Approved":
            header_id = approved_routing.get(li["product_id"])
        if (so, ln) in missing_bom:
            issues.append(DataIssue(so, ln, "missing_bom", f"{product['product_code']} has no approved BOM"))
            continue
        if not header_id or not rops_by_header.get(header_id):
            issues.append(DataIssue(so, ln, "missing_routing", f"{product['product_code']} has no approved routing"))
            continue
        ops = routing_ops(header_id)
        unqualified = [o.number for o in ops
                       if not any(m.active and m.work_center == o.work_center and o.capabilities <= m.capabilities
                                  for m in machines.values())]
        if unqualified:
            issues.append(DataIssue(so, ln, "no_qualified_machine",
                                    f"No active qualified machine for {', '.join(unqualified)} ({product['product_code']})"))
            continue
        uncalendared = [o.number for o in ops
                        if not any(m.active and m.work_center == o.work_center and o.capabilities <= m.capabilities
                                   and (machine_shifts.get(m.code) or wc_shifts.get(m.work_center))
                                   for m in machines.values())]
        if uncalendared:
            issues.append(DataIssue(so, ln, "no_calendar",
                                    f"No shift calendar for the machines of {', '.join(uncalendared)}"))
            continue

        requested = _day(li.get("requested_delivery_date") or order["requested_delivery_date"])
        job = Job(order_number=so, line_number=ln, item=product["product_code"], quantity=float(li["quantity"]),
                  priority=li.get("priority") or order["priority"], due=local_midnight(requested + timedelta(days=1)),
                  ops=ops, production_order=production_order_number(so, ln), status=li["status"],
                  baseline_finish=base_finish.get(li["id"]))
        numbers = {o.number for o in ops}
        for bop in base_ops.get(li["id"], []):
            n = bop["operation_number"]
            if n not in numbers or bop["status"] == "Cancelled":
                continue
            if bop["status"] == "Completed":
                job.completed[n] = _ts(bop.get("actual_finish")) or _ts(bop.get("planned_finish")) or as_of
                continue
            start, end = _ts(bop.get("planned_start")), _ts(bop.get("planned_finish"))
            machine = machine_code.get(bop.get("assigned_machine_id"))
            if not (start and end and machine):
                continue
            job.baseline[n] = (machine, start, end)
            if bop["status"] in FIXED_OP_STATUSES or bop["is_frozen"]:
                job.fixed[n] = FixedOp(machine, start, end, "in progress" if bop["status"] == "In Progress"
                                       else bop["status"].lower() if bop["status"] in FIXED_OP_STATUSES else "frozen")
            elif options.freeze_days and start < freeze_until:
                job.fixed[n] = FixedOp(machine, start, end, f"inside {options.freeze_days}-day freeze window")
        if len(job.completed) == len(ops):
            notes.append(f"{job.key}: all operations completed")
            continue

        # Material readiness from MRP (level-1 requirements gate their consuming operation).
        first_op = next(o.number for o in ops if not o.predecessors)
        for r in mrp.requirements:
            if (r.order_number, r.line_number) != (so, ln) or r.level != 1:
                continue
            target = r.operation_ref if r.operation_ref in numbers else first_op
            if target in job.completed:
                continue
            if r.status == "short":
                job.material_ready[target] = None
                note = f"{r.item} short {mrp.shortage_units(r):g}"
                job.material_notes[target] = "; ".join(filter(None, [job.material_notes.get(target), note]))
            elif target not in job.material_ready or job.material_ready[target] is not None:
                ready = local_midnight(r.ready_date) if r.ready_date else as_of
                current = job.material_ready.get(target)
                if current is None or ready > current:
                    job.material_ready[target] = max(ready, as_of)
                    if ready > as_of:
                        src = ", ".join(a.ref for a in r.allocations if a.available == r.ready_date) or "supply"
                        job.material_notes[target] = f"{r.item} from {src} on {r.ready_date:%b %d}"
        jobs.append(job)

    return SchedulingInput(as_of=as_of, horizon_end=local_midnight(end_day + timedelta(days=1)), machines=machines,
                           wc_shifts=dict(wc_shifts), machine_shifts=dict(machine_shifts), exceptions=exceptions,
                           downtime=downtime, jobs=jobs, issues=issues, options=options,
                           snapshot_at=snap.get("generated_at"), notes=notes)
