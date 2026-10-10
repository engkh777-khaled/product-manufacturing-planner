"""Plant-data quality checks (spec Phase 6 "data validation", §11.3 rule 3), M12.

Finds data that would make plans wrong or impossible, before anyone relies on them. Pure function of one
`planning_snapshot()` document; severities: error (planning is wrong or blocked), warning (likely wrong), info.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date

OPEN_ORDER = {"Draft", "Confirmed", "Planning", "Planned", "In Production"}
OPEN_PO = {"Open", "In Transit", "Partially Received"}


@dataclass(frozen=True)
class Finding:
    severity: str        # error | warning | info
    category: str
    entity: str
    message: str

    def as_dict(self) -> dict:
        return asdict(self)


def _day(v) -> date | None:
    return date.fromisoformat(v[:10]) if v else None


def check(snap: dict, as_of: date | None = None) -> list[Finding]:
    out: list[Finding] = []
    add = lambda sev, cat, ent, msg: out.append(Finding(sev, cat, ent, msg))  # noqa: E731
    products = {p["id"]: p for p in snap["products"]}
    code = {pid: p["product_code"] for pid, p in products.items()}
    wc_code = {w["id"]: w["work_center_code"] for w in snap["work_centers"]}
    if as_of is None:
        base = next((v for v in snap["schedule_versions"] if v["is_baseline"]), None)
        run = next((r for r in snap["planning_runs"] if base and r["id"] == base["planning_run_id"]), None)
        as_of = _day(run["planning_horizon_start"]) if run else date.today()

    approved_bom = {h["product_id"] for h in snap["bom_headers"] if h["status"] == "Approved"}
    approved_routing = {h["product_id"]: h["id"] for h in snap["routing_headers"] if h["status"] == "Approved"}

    # engineering completeness of products that are ordered
    orders = {o["id"]: o for o in snap["sales_orders"]}
    for li in snap["sales_order_items"]:
        o = orders[li["sales_order_id"]]
        if o["status"] not in OPEN_ORDER or li["status"] not in OPEN_ORDER:
            continue
        ref = f"{o['order_number']}/{li['line_number']}"
        p = products[li["product_id"]]
        if li["product_id"] not in approved_bom:
            add("error", "engineering", ref, f"{p['product_code']} has no approved BOM — the line cannot be planned")
        if li["product_id"] not in approved_routing:
            add("error", "engineering", ref, f"{p['product_code']} has no approved routing — the line cannot be scheduled")
        if p.get("product_status") not in (None, "Active"):
            add("warning", "engineering", ref, f"ordered product {p['product_code']} is {p.get('product_status')}")
        requested = _day(li.get("requested_delivery_date") or o.get("requested_delivery_date"))
        if requested and requested < as_of and o["status"] != "Draft":
            add("warning", "demand", ref, f"requested date {requested} is before the planning date {as_of}")
        if requested and o.get("order_date") and requested < _day(o["order_date"]):
            add("error", "demand", ref, "requested date is before the order date")
        if float(li["quantity"]) <= 0:
            add("error", "demand", ref, "quantity must be positive")

    # BOM components
    for bi in snap["bom_items"]:
        comp = products.get(bi["component_product_id"])
        if comp and comp.get("product_status") not in (None, "Active"):
            head = next(h for h in snap["bom_headers"] if h["id"] == bi["bom_header_id"])
            if head["status"] == "Approved":
                add("warning", "engineering", f"{code[head['product_id']]} {head['revision']} line {bi['line_number']}",
                    f"approved BOM uses {comp['product_code']} which is {comp['product_status']}")
    for p in snap["products"]:
        if p["item_type"] == "manufactured" and not float(p.get("standard_lead_time_days") or 0) and p["id"] in approved_bom:
            add("info", "engineering", p["product_code"], "sub-assembly has no standard lead time (MRP assumes 0 days to build)")

    # resources: every routing operation needs a qualified, active, calendared machine
    shifts_wc = {s["work_center_id"] for s in snap.get("shift_calendars", []) if s.get("work_center_id")}
    shifts_m = {s["machine_id"] for s in snap.get("shift_calendars", []) if s.get("machine_id")}
    for wid, wcode in wc_code.items():
        machines = [m for m in snap["machines"] if m["work_center_id"] == wid and m["is_active"]]
        if machines and wid not in shifts_wc and not all(m["id"] in shifts_m for m in machines):
            add("error", "calendar", wcode, "work center has active machines but no shift calendar — nothing can be scheduled")
    routing_owner = {h["id"]: h for h in snap["routing_headers"]}
    for o in snap["routing_operations"]:
        head = routing_owner[o["routing_header_id"]]
        if head["status"] != "Approved":
            continue
        caps = set(o.get("required_capabilities") or [])
        ok = [m for m in snap["machines"] if m["work_center_id"] == o["work_center_id"] and m["is_active"]
              and caps <= set(m.get("capability_tags") or [])]
        if not ok:
            add("error", "resources", f"{code[head['product_id']]} {o['operation_number']}",
                f"no active machine in {wc_code[o['work_center_id']]} has capabilities {sorted(caps)}")
        if float(o["cycle_time_minutes"]) <= 0 and float(o["setup_time_minutes"]) <= 0:
            add("warning", "engineering", f"{code[head['product_id']]} {o['operation_number']}", "operation has no time")

    # supply
    po_number = {p["id"]: p["po_number"] for p in snap["purchase_orders"]}
    for li in snap["purchase_order_items"]:
        if li["status"] not in OPEN_PO or float(li["open_quantity"]) <= 0:
            continue
        ref = f"{po_number[li['purchase_order_id']]}/{li['line_number']}"
        expected = _day(li.get("expected_delivery_date"))
        if expected is None:
            add("error", "supply", ref, f"open receipt of {code[li['product_id']]} has no expected date — MRP cannot count it")
        elif expected < as_of:
            add("warning", "supply", ref, f"expected {expected}, before the planning date, and still open — confirm with the supplier")
    for b in snap["inventory_balances"]:
        on_hand, res, hold = (float(b[k]) for k in ("on_hand_quantity", "reserved_quantity", "quality_hold_quantity"))
        if min(on_hand, res, hold) < 0 or res + hold > on_hand:
            add("error", "inventory", f"{code[b['product_id']]} {b.get('lot_number') or ''}".strip(),
                f"impossible balance: on hand {on_hand:g}, reserved {res:g}, QC hold {hold:g}")

    # baseline uses inactive machines
    machine = {m["id"]: m for m in snap["machines"]}
    base = next((v for v in snap["schedule_versions"] if v["is_baseline"]), None)
    base_pos = {p["id"] for p in snap["production_orders"] if base and p["schedule_version_id"] == base["id"]}
    for op in snap["production_operations"]:
        m = machine.get(op.get("assigned_machine_id"))
        if op["production_order_id"] in base_pos and m and not m["is_active"] and op["status"] not in ("Completed", "Cancelled"):
            add("error", "resources", f"baseline {op['operation_number']}", f"planned on inactive machine {m['machine_code']}")

    order = {"error": 0, "warning": 1, "info": 2}
    return sorted(out, key=lambda f: (order[f.severity], f.category, f.entity))


def summary(findings: list[Finding]) -> dict:
    counts = defaultdict(int)
    for f in findings:
        counts[f.severity] += 1
    return {"error": counts["error"], "warning": counts["warning"], "info": counts["info"]}
