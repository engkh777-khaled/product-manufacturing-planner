"""MRP engine (spec §7) — level-by-level netting with explicit, priority-based allocation.

1. Level 1: every open order line with an approved BOM is exploded into component requirements
   (gross = parent quantity × quantity per ÷ (1 − scrap); scrap applied once, spec §7.1). Lines without an
   approved BOM/routing are reported as "needs data" (spec acceptance test 8).
2. All requirements of the level are allocated **across orders**: per item, by order priority
   (Critical → Low), then need date, then requested date, then order/line. Each requirement takes the
   earliest-available remaining supply (stock at as-of, then eligible PO receipts by date). A unit of supply is
   allocated exactly once (spec acceptance test 10) and demand for shared components is consolidated (§7.1).
3. The unmet quantity of a manufactured component that has its own approved BOM (a sub-assembly) is planned for
   production and exploded into the next level, needed the sub-assembly's lead time earlier (netting at every level).
4. Bottom-up: a sub-assembly is ready when all its children are (plus lead time); if any child is short, it is short.

Need date = start of the consuming operation in the approved baseline schedule (BOM line `operation_reference`),
else the production order start; never before as-of (spec §7.3: a component arriving next week is not available today).
"""

from __future__ import annotations

import copy
from datetime import date, timedelta

from planning.mrp.model import EPS, PRIORITY_RANK, Allocation, Demand, LineIssue, MrpInput, MrpResult, Requirement, SupplyLot


def run_mrp(inp: MrpInput) -> MrpResult:
    supplies = {item: [copy.copy(lot) for lot in lots] for item, lots in inp.supplies.items()}
    for lots in supplies.values():
        for lot in lots:
            lot.remaining = lot.quantity

    issues: list[LineIssue] = []
    level_reqs: list[tuple[Demand, Requirement]] = []
    for demand in sorted(inp.demands, key=lambda d: d.sort_key):
        if demand.item not in inp.boms:
            issues.append(LineIssue(demand.order_number, demand.line_number, "missing_bom",
                                    f"{demand.item} has no approved BOM revision"))
            continue
        if not demand.has_routing:
            issues.append(LineIssue(demand.order_number, demand.line_number, "missing_routing",
                                    f"{demand.item} has no approved routing"))
        level_reqs += [(demand, req) for req in _explode_one_level(inp, demand, demand.item, demand.quantity,
                                                                   demand.start_date, level=1)]

    all_reqs: list[Requirement] = []
    seq = 0
    while level_reqs:
        # Allocate the whole level across orders: priority first, then need date (earliest need first).
        for demand, req in sorted(level_reqs, key=lambda dr: (PRIORITY_RANK.get(dr[1].priority, 9), dr[1].need_date,
                                                             dr[0].requested_date, dr[1].order_number,
                                                             dr[1].line_number, dr[1].item)):
            req.seq, seq = seq, seq + 1
            _allocate(supplies.get(req.item, []), req, inp.as_of)
        all_reqs += [req for _, req in level_reqs]

        next_level: list[tuple[Demand, Requirement]] = []
        for demand, req in level_reqs:
            item = inp.items.get(req.item)
            if req.shortage > EPS and item and item.item_type == "manufactured" and req.item in inp.boms:
                req.planned_production, req.shortage = req.shortage, 0.0
                req.lead_days = round(item.lead_time_days)
                child_need = max(req.need_date - timedelta(days=req.lead_days), inp.as_of)
                req.children = _explode_one_level(inp, demand, req.item, req.planned_production, child_need,
                                                  level=req.level + 1)
                next_level += [(demand, child) for child in req.children]
        level_reqs = next_level

    for req in sorted(all_reqs, key=lambda r: -r.level):   # deepest first
        if req.planned_production > 0:
            if any(c.status == "short" for c in req.children):
                req.shortage, req.ready_date = req.planned_production, None   # cannot be built yet
            else:
                built = max([c.ready_date for c in req.children if c.ready_date] + [inp.as_of])
                built += timedelta(days=req.lead_days)
                req.ready_date = max(req.ready_date or inp.as_of, built)

    all_reqs.sort(key=lambda r: (r.order_number, r.line_number, r.level, r.item))
    return MrpResult(input=inp, requirements=all_reqs, issues=issues)


def _explode_one_level(inp: MrpInput, demand: Demand, parent: str, quantity: float, parent_need: date,
                       level: int) -> list[Requirement]:
    reqs = []
    for line in inp.boms.get(parent, []):
        if level == 1 and line.operation_ref and line.operation_ref in demand.op_dates:
            need = demand.op_dates[line.operation_ref]
        else:
            need = parent_need
        reqs.append(Requirement(order_number=demand.order_number, line_number=demand.line_number,
                                priority=demand.priority, item=line.component, parent=parent, level=level,
                                gross=quantity * line.qty_per / (1 - line.scrap), need_date=max(need, inp.as_of),
                                operation_ref=line.operation_ref if level == 1 else None))
    return reqs


def _allocate(lots: list[SupplyLot], req: Requirement, as_of: date) -> None:
    needed = req.gross
    for lot in lots:
        if needed <= EPS:
            break
        if lot.remaining <= EPS:
            continue
        take = min(lot.remaining, needed)
        lot.remaining -= take
        needed -= take
        req.allocations.append(Allocation(lot.ref, lot.source, take, lot.available))
    if needed > EPS:
        req.shortage, req.ready_date = needed, None
    else:
        req.shortage = 0.0
        req.ready_date = max([a.available for a in req.allocations] + [as_of])


def projection(result: MrpResult, item: str) -> list[dict]:
    """Time-phased availability of one item (spec §7.3): supply in, requirements out, running balance by date.

    A negative balance means the item is needed before enough supply has arrived.
    """
    inp = result.input
    events = []
    for lot in inp.supplies.get(item, []):
        events.append({"date": lot.available, "kind": "stock" if lot.source == "stock" else "receipt",
                       "ref": lot.ref, "qty": lot.quantity})
    for r in result.requirements:
        if r.item == item:
            events.append({"date": r.need_date, "kind": "requirement", "ref": f"{r.order_number}/{r.line_number}",
                           "qty": -r.gross})
    events.sort(key=lambda e: (e["date"], e["qty"] < 0))
    balance = 0.0
    for e in events:
        balance += e["qty"]
        e["balance"] = balance
    return events
