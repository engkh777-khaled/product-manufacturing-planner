"""Turn an MRP result into planning alerts (spec §7.4) and the payload of `public.record_mrp_run()`.

Every alert says which order is affected, which component, required / available / shortage quantities,
the required date, the expected availability date (if known), the impact on production and corrective actions
(spec §7.5). Alerts are raised for purchased/stocked items only; a sub-assembly that cannot be built is explained
through its missing children, so problems are not reported twice.
"""

from __future__ import annotations

from datetime import datetime, timezone

from planning.mrp.model import MrpResult, Requirement

ALGORITHM_VERSION = "mrp-1.0 (priority allocation, multilevel netting)"


def _higher_priority_consumers(result: MrpResult, req: Requirement) -> list[str]:
    """Other orders allocated this item before this requirement (explains the allocation decision, test 10)."""
    earlier = sorted((r for r in result.requirements
                      if r.item == req.item and r.seq < req.seq and r.allocations and r.order_number != req.order_number),
                     key=lambda r: r.seq)
    return list(dict.fromkeys(r.order_number for r in earlier))


def build_alerts(result: MrpResult) -> list[dict]:
    items = result.input.items
    alerts: list[dict] = []
    for req in result.requirements:
        if req.planned_production > 0 or req.status == "covered":
            continue
        item = items.get(req.item)
        name = item.name if item else req.item
        uom = item.uom if item else "EA"
        op = f" at {req.operation_ref}" if req.operation_ref else ""
        base = {"order_number": req.order_number, "line_number": req.line_number}
        if req.status == "short":
            units = result.shortage_units(req)
            holds = result.input.holds.get(req.item, [])
            competing = _higher_priority_consumers(result, req)
            actions = []
            if holds:
                actions.append("Release QC hold " + ", ".join(f"{ref} ({qty:g})" for ref, qty in holds))
            actions.append(f"Expedite or raise a purchase order for {units:g} {uom}")
            if competing:
                actions.append(f"Re-prioritise against {', '.join(competing)} or reschedule {req.order_number}")
            else:
                actions.append(f"Reschedule {req.order_number} after material availability")
            description = (f"{req.order_number} line {req.line_number}: {req.item} ({name}) short {units:g} {uom} — "
                           f"required {req.gross:.2f} by {req.need_date:%Y-%m-%d}{op}, available {req.allocated:.2f}; "
                           f"no eligible receipt.")
            if competing:
                description += f" Available stock was allocated to higher-priority {', '.join(competing)}."
            alerts.append({**base, "alert_type": "material_shortage", "severity": "critical",
                           "description": description, "recommended_action": "; ".join(actions) + ".",
                           "details": {"component": req.item, "required": round(req.gross, 3),
                                       "available": round(req.allocated, 3), "shortage": units,
                                       "required_date": req.need_date.isoformat(), "expected_date": None,
                                       "operation": req.operation_ref, "allocated_to_higher_priority": competing,
                                       "impact": f"{req.operation_ref or 'Production'} of {req.order_number} cannot start"}})
            if holds:
                alerts.append({**base, "alert_type": "quality_hold", "severity": "warning",
                               "description": f"{req.item}: {sum(q for _, q in holds):g} {uom} on QC hold "
                                              f"({', '.join(ref for ref, _ in holds)}) could cover the shortage of "
                                              f"{req.order_number} once released.",
                               "recommended_action": "Expedite inspection / PMI release.",
                               "details": {"component": req.item, "holds": [{"ref": r, "qty": q} for r, q in holds]}})
        else:  # late
            receipts = [a for a in req.allocations if a.source == "po"]
            po = receipts[-1].ref.split("/")[0] if receipts else None
            description = (f"{req.order_number} line {req.line_number}: {req.item} ({name}) available "
                           f"{req.ready_date:%Y-%m-%d}" + (f" via {po}" if po else "") +
                           f" — needed {req.need_date:%Y-%m-%d}{op} (+{req.days_late} days).")
            alerts.append({**base, "alert_type": "late_supplier_receipt", "severity": "critical",
                           "description": description,
                           "recommended_action": (f"Expedite {po} to {req.need_date:%Y-%m-%d}" if po else "Expedite supply")
                                                 + f", or reschedule {req.operation_ref or 'the order'} after {req.ready_date:%Y-%m-%d}.",
                           "details": {"component": req.item, "po_number": po, "required": round(req.gross, 3),
                                       "required_date": req.need_date.isoformat(),
                                       "expected_date": req.ready_date.isoformat(), "days_late": req.days_late,
                                       "operation": req.operation_ref,
                                       "impact": f"{req.operation_ref or 'Production'} of {req.order_number} waits for material"}})
    for issue in result.issues:
        alerts.append({"order_number": issue.order_number, "line_number": issue.line_number, "alert_type": issue.kind,
                       "severity": "warning", "description": f"{issue.order_number} line {issue.line_number}: {issue.detail}.",
                       "recommended_action": "Engineering to release the missing data; the line is not production-ready.",
                       "details": {}})
    for ref, item, qty in result.input.undated_receipts:
        alerts.append({"order_number": None, "line_number": None, "alert_type": "material_shortage", "severity": "warning",
                       "description": f"{ref} ({item}, {qty:g} open) has no expected delivery date and is not counted as supply.",
                       "recommended_action": "Purchasing to confirm a delivery date with the supplier.",
                       "details": {"component": item, "po_line": ref}})
    return alerts


def run_payload(result: MrpResult, started_at: datetime, seconds: float) -> dict:
    """Body for `public.record_mrp_run(p jsonb)`."""
    inp = result.input
    return {
        "horizon_start": inp.horizon[0].isoformat(), "horizon_end": inp.horizon[1].isoformat(),
        "algorithm_version": ALGORITHM_VERSION, "status": result.status,
        "started_at": started_at.astimezone(timezone.utc).isoformat(), "seconds": round(seconds, 3),
        "input": {"snapshot_generated_at": inp.snapshot_at, "as_of": inp.as_of.isoformat(),
                  "order_lines": len(inp.demands), "items_with_supply": len(inp.supplies),
                  "requirements": len(result.requirements), "problems": len(result.problems())},
        "alerts": build_alerts(result),
    }
