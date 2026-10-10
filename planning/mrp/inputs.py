"""Build MRP inputs from a `public.planning_snapshot()` document (one consistent MVCC snapshot, spec §4).

Supply eligibility follows spec §6.11 / §7.2: stock counts at its net-available quantity (on hand − reserved −
QC hold); an open PO line counts from its expected date (+ inspection days) only if it is not cancelled, not
rejected and has a date. Need dates come from the approved baseline schedule (start of the consuming operation);
lines without scheduled operations fall back to the production order start, then to the requested date minus the
product's standard lead time.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from planning.mrp.model import BomLine, Demand, Item, MrpInput, SupplyLot

PLANT_TZ = ZoneInfo("America/Chicago")
OPEN_DEMAND = {"Draft", "Confirmed", "Planning", "Planned", "In Production"}
ELIGIBLE_PO_STATUS = {"Open", "In Transit", "Partially Received"}


def _day(value: str | None) -> date | None:
    return date.fromisoformat(value[:10]) if value else None


def _local_day(value: str | None) -> date | None:
    return datetime.fromisoformat(value).astimezone(PLANT_TZ).date() if value else None


def _num(value) -> float:
    return float(value) if value is not None else 0.0


def from_snapshot(snap: dict, as_of: date | None = None, inspection_days: int = 0) -> MrpInput:
    products = {p["id"]: p for p in snap["products"]}
    items = {p["product_code"]: Item(code=p["product_code"], name=p["name"], item_type=p["item_type"],
                                     uom=p.get("unit_of_measure") or "EA",
                                     lead_time_days=_num(p.get("standard_lead_time_days")))
             for p in snap["products"]}
    code = {pid: p["product_code"] for pid, p in products.items()}

    # Approved BOMs only (spec §6.2): these drive executable planning.
    approved = {h["id"]: code[h["product_id"]] for h in snap["bom_headers"] if h["status"] == "Approved"}
    boms: dict[str, list[BomLine]] = defaultdict(list)
    for bi in sorted(snap["bom_items"], key=lambda x: x["line_number"]):
        if bi["bom_header_id"] in approved:
            boms[approved[bi["bom_header_id"]]].append(BomLine(
                component=code[bi["component_product_id"]], qty_per=_num(bi["quantity_per_parent"]),
                scrap=_num(bi["scrap_factor"]), operation_ref=bi.get("operation_reference")))

    # Baseline schedule and planning horizon.
    versions = snap["schedule_versions"]
    baseline = next((v for v in versions if v["is_baseline"]), None)
    runs = {r["id"]: r for r in snap["planning_runs"]}
    base_run = runs.get(baseline["planning_run_id"]) if baseline else None
    horizon_start = _day(base_run["planning_horizon_start"]) if base_run else (as_of or date.today())
    horizon_end = _day(base_run["planning_horizon_end"]) if base_run else horizon_start + timedelta(days=28)
    as_of = as_of or horizon_start

    # Supply.
    warehouses = {w["id"]: w["warehouse_code"] for w in snap["warehouses"]}
    supplies: dict[str, list[SupplyLot]] = defaultdict(list)
    holds: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for b in snap["inventory_balances"]:
        item = code[b["product_id"]]
        ref = warehouses[b["warehouse_id"]] + (f" lot {b['lot_number']}" if b["lot_number"] else "")
        net = _num(b["on_hand_quantity"]) - _num(b["reserved_quantity"]) - _num(b["quality_hold_quantity"])
        if net > 0:
            supplies[item].append(SupplyLot(item, net, as_of, "stock", ref))
        if _num(b["quality_hold_quantity"]) > 0:
            holds[item].append((ref, _num(b["quality_hold_quantity"])))
    po_numbers = {p["id"]: p["po_number"] for p in snap["purchase_orders"]}
    undated = []
    for li in snap["purchase_order_items"]:
        item = code[li["product_id"]]
        open_qty = _num(li["open_quantity"])
        ref = f"{po_numbers[li['purchase_order_id']]}/{li['line_number']}"
        if open_qty <= 0 or li["status"] not in ELIGIBLE_PO_STATUS or li["quality_status"] == "Rejected":
            continue
        expected = _day(li.get("expected_delivery_date"))
        if expected is None:
            undated.append((ref, item, open_qty))   # spec §7.4: missing supplier date is an alert, not supply
            continue
        supplies[item].append(SupplyLot(item, open_qty, max(expected + timedelta(days=inspection_days), as_of), "po", ref))
    for lots in supplies.values():
        lots.sort(key=lambda lot: (lot.available, lot.source != "stock", lot.ref))

    # Demand: open sales order lines with need dates from the baseline schedule.
    orders = {o["id"]: o for o in snap["sales_orders"]}
    routed = {r["product_id"] for r in snap["routing_headers"] if r["status"] == "Approved"}
    prod_orders = {po["id"]: po for po in snap["production_orders"]
                   if baseline and po["schedule_version_id"] == baseline["id"]}
    start_by_line, ops_by_line = {}, defaultdict(dict)
    for po in prod_orders.values():
        if po.get("sales_order_item_id") and po.get("planned_start"):
            start_by_line[po["sales_order_item_id"]] = _local_day(po["planned_start"])
    for op in snap["production_operations"]:
        po = prod_orders.get(op["production_order_id"])
        if po and op.get("planned_start") and po.get("sales_order_item_id"):
            ops_by_line[po["sales_order_item_id"]][op["operation_number"]] = _local_day(op["planned_start"])

    demands = []
    for li in snap["sales_order_items"]:
        order = orders[li["sales_order_id"]]
        if order["status"] not in OPEN_DEMAND or li["status"] not in OPEN_DEMAND:
            continue
        item = code[li["product_id"]]
        requested = _day(li.get("requested_delivery_date") or order["requested_delivery_date"])
        start = start_by_line.get(li["id"]) or (requested - timedelta(days=round(items[item].lead_time_days)))
        demands.append(Demand(
            order_number=order["order_number"], line_number=li["line_number"], item=item,
            quantity=_num(li["quantity"]), requested_date=requested, priority=li.get("priority") or order["priority"],
            status=li["status"], start_date=start, op_dates=dict(ops_by_line.get(li["id"], {})),
            has_routing=li["product_id"] in routed and bool(li.get("routing_revision_id"))))

    return MrpInput(as_of=as_of, horizon=(horizon_start, horizon_end), items=items, boms=dict(boms),
                    supplies=dict(supplies), demands=demands, holds=dict(holds), undated_receipts=undated,
                    snapshot_at=snap.get("generated_at"))
