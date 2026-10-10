"""One scheduling run end to end: snapshot → inputs → CP-SAT → explanation → independent validation → payload for
`public.record_schedule_run()` (spec §6.18–6.19). Pure function of its arguments, so it runs in a worker process."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone

from planning.scheduling.calendar import subtract, working_windows
from planning.scheduling.inputs import from_snapshot
from planning.scheduling.model import Options, Schedule, SchedulingInput, Weights
from planning.scheduling.solver import ALGORITHM_VERSION, solve
from planning.validation import ValidationReport, validate


def plan(snap: dict, horizon: tuple[date, date] | None = None, options: Options | None = None) -> tuple[Schedule, ValidationReport]:
    inp = from_snapshot(snap, horizon, options)
    sched = solve(inp)
    report = validate(sched) if sched.ops or sched.lines else ValidationReport()
    if sched.outcome in ("Infeasible", "Solver limit reached", "Failed"):
        report.violations.clear()   # nothing to validate; the outcome itself blocks approval
    return sched, report


def _iso(t: datetime | None) -> str | None:
    return t.astimezone(timezone.utc).isoformat() if t else None


def _overlap_minutes(windows, a: datetime, b: datetime) -> float:
    return sum(max(0.0, (min(y, b) - max(x, a)).total_seconds() / 60) for x, y in windows)


def loads(sched: Schedule) -> tuple[dict, dict]:
    """Scheduled vs available working hours inside the planning horizon, per work center and machine."""
    inp = sched.input
    a, b = inp.as_of, inp.horizon_end
    windows = {code: subtract(working_windows(inp, m, a, b),
                              [(d.start, d.end) for d in inp.downtime if d.machine == code and d.status == "Confirmed"])
               for code, m in inp.machines.items()}
    busy = defaultdict(float)
    for o in sched.ops:
        if o.machine in windows:
            busy[o.machine] += _overlap_minutes(windows[o.machine], max(o.start, a), min(o.end, b))
    wc, util = defaultdict(lambda: {"scheduled_hours": 0.0, "capacity_hours": 0.0, "operations": 0}), {}
    for code, m in inp.machines.items():
        cap = _overlap_minutes(windows[code], a, b)
        util[code] = round(100 * busy[code] / cap, 1) if cap else 0.0
        w = wc[m.work_center]
        w["scheduled_hours"] = round(w["scheduled_hours"] + busy[code] / 60, 1)
        w["capacity_hours"] = round(w["capacity_hours"] + cap / 60, 1)
    for o in sched.ops:
        if o.machine in inp.machines and o.start < b:
            wc[inp.machines[o.machine].work_center]["operations"] += 1
    for code, w in wc.items():
        pct = 100 * w["scheduled_hours"] / w["capacity_hours"] if w["capacity_hours"] else 0
        w["queue_note"] = f"{w['operations']} ops in horizon • {pct:.0f}% loaded"
    return dict(wc), util


def _line_dict(line, tz) -> dict:
    change = None
    if line.completion and line.baseline_finish:
        change = round((line.completion - line.baseline_finish).total_seconds() / 86400, 1)
    return {"order_number": line.order_number, "line_number": line.line_number, "item": line.item,
            "priority": line.priority, "status": line.status, "due": _iso(line.due), "completion": _iso(line.completion),
            "lateness_hours": line.lateness_hours, "earliest_possible": _iso(line.earliest_possible),
            "achievable": line.achievable, "blocked_ops": line.blocked_ops, "limiting": line.limiting,
            "baseline_finish": _iso(line.baseline_finish), "change_vs_baseline_days": change}


def payload(sched: Schedule, report: ValidationReport, started_at: datetime) -> dict:
    inp = sched.input
    lines = [_line_dict(l, inp.tz) for l in sched.lines]
    late_orders = sorted({l.order_number for l in sched.lines if l.status in ("late", "blocked")})
    wc_load, util = loads(sched) if sched.ops else ({}, {})
    blocked = [l for l in sched.lines if l.status == "blocked"]
    late = [l for l in sched.lines if l.status == "late"]
    passed = report.passed and sched.outcome not in ("Infeasible", "Solver limit reached", "Failed")
    orders = []
    for job in inp.jobs:
        ops = sched.ops_for(job.key)
        if not ops and not job.completed:
            continue
        rows = []
        rid = {op.number: op.routing_op_id for op in job.ops}
        for o in ops:
            note = job.fixed[o.op_number].reason.capitalize() if o.fixed else ""
            ready = job.material_ready.get(o.op_number)
            if not note and ready and ready > inp.as_of:
                note = f"Material: {job.material_notes.get(o.op_number, '')}".strip()
            rows.append({"operation_number": o.op_number, "routing_operation_id": rid[o.op_number],
                         "machine_code": o.machine, "planned_start": _iso(o.start), "planned_finish": _iso(o.end),
                         "is_frozen": o.fixed, "notes": note[:200] or None})
        for n, finish in job.completed.items():
            rows.append({"operation_number": n, "routing_operation_id": rid[n], "status": "Completed",
                         "actual_finish": _iso(finish)})
        for n, why in job.blocked_ops().items():
            rows.append({"operation_number": n, "routing_operation_id": rid[n], "status": "Planned",
                         "notes": f"Not scheduled: {why}"[:200]})
        starts = [o.start for o in ops]
        orders.append({"order_number": job.order_number, "line_number": job.line_number,
                       "production_order_number": job.production_order, "product_code": job.item,
                       "quantity": job.quantity, "planned_start": _iso(min(starts)) if starts else None,
                       "planned_finish": _iso(max(o.end for o in ops)) if ops and not job.blocked_ops() else None,
                       "status": "In Progress" if job.completed else "Planned", "operations": rows})
    summary = (f"CP-SAT: {len(inp.jobs)} lines — {len(late)} late, {len(blocked)} blocked by material"
               + (f", {len(inp.issues)} need data" if inp.issues else "")
               + f"; validation {'passed' if passed else 'FAILED'}")
    o = inp.options
    return {
        "horizon_start": inp.as_of.astimezone(inp.tz).date().isoformat(),
        "horizon_end": (inp.horizon_end.astimezone(inp.tz) - timedelta(minutes=1)).date().isoformat(),
        "algorithm_version": ALGORITHM_VERSION, "status": sched.outcome, "objective": sched.objective,
        "seconds": round(sched.seconds, 3), "started_at": _iso(started_at),
        "input": {"snapshot_at": inp.snapshot_at, "as_of": _iso(inp.as_of), "lines": len(inp.jobs),
                  "operations": sum(len(j.ops) for j in inp.jobs), "machines": len(inp.machines),
                  "options": {"time_limit_s": o.time_limit_s, "freeze_days": o.freeze_days,
                              "include_drafts": o.include_drafts, "slot_minutes": o.slot_minutes,
                              "orders": sorted(o.orders) if o.orders else None, "weights": asdict(o.weights)},
                  "solver_status": sched.solver_status},
        "version": {
            "label": f"Solver run — {sched.outcome}", "change_summary": summary, "validation_passed": passed,
            "metrics": {"late_orders": len(late_orders), "late_order_numbers": late_orders,
                        "total_lateness_hours": round(sum(l.lateness_hours for l in late), 1), "overtime_cost": 0,
                        "hard_violations": len(report.violations), "soft_violations": len(late) + len(blocked),
                        "work_center_load": wc_load, "machine_utilization": util, "lines": lines,
                        "validation": report.as_dict(), "conflicts": sched.conflicts,
                        "issues": [asdict(i) for i in inp.issues], "outcome": sched.outcome,
                        "load_source": f"{ALGORITHM_VERSION}"}},
        "orders": orders,
    }


def plan_payload(snap: dict, horizon: tuple[str, str] | None = None, options: dict | None = None) -> dict:
    """Worker-process entry point: plain JSON in, plain JSON out."""
    started = datetime.now(timezone.utc)
    opts = Options(**{k: v for k, v in (options or {}).items() if k not in ("weights", "orders")})
    if options and options.get("weights"):
        opts.weights = Weights(**options["weights"])
    if options and options.get("orders"):
        opts.orders = frozenset(options["orders"])
    hz = (date.fromisoformat(horizon[0]), date.fromisoformat(horizon[1])) if horizon else None
    sched, report = plan(snap, hz, opts)
    return payload(sched, report, started)


def summarize(inp: SchedulingInput) -> str:
    return f"{len(inp.jobs)} lines, {sum(len(j.ops) for j in inp.jobs)} operations, {len(inp.machines)} machines"
