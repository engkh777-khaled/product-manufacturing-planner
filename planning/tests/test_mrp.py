"""M08 test gate: MRP engine. Pure tests — no database, no network.

Synthetic cases prove each rule; golden tests on the recorded snapshot (tests/fixtures/snapshot.json, fictional)
cover spec acceptance tests 2 (missing critical component), 3 (late supplier receipt) and 10 (competing orders).
"""

import json
from datetime import date
from pathlib import Path

import pytest

from planning.mrp import from_snapshot, run_mrp
from planning.mrp.alerts import build_alerts, run_payload
from planning.mrp.engine import projection
from planning.mrp.model import BomLine, Demand, Item, MrpInput, SupplyLot

AS_OF = date(2025, 11, 15)
SNAPSHOT = json.loads((Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "snapshot.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ synthetic builder

def make_input(boms, supplies=(), demands=(), items=None, holds=None, undated=()):
    items = items or {}
    codes = {c for parent, lines in boms.items() for c in [parent] + [l.component for l in lines]}
    all_items = {c: items.get(c, Item(c, c, "purchased")) for c in codes}
    all_items.update(items)
    by_item = {}
    for lot in supplies:
        by_item.setdefault(lot.item, []).append(lot)
    for lots in by_item.values():
        lots.sort(key=lambda l: (l.available, l.source != "stock"))
    return MrpInput(as_of=AS_OF, horizon=(AS_OF, date(2025, 12, 15)), items=all_items, boms=boms, supplies=by_item,
                    demands=list(demands), holds=holds or {}, undated_receipts=list(undated))


def demand(order, qty, priority="Normal", line=1, start=date(2025, 11, 20), item="FG", requested=date(2025, 12, 1), ops=None):
    return Demand(order, line, item, qty, requested, priority, "Confirmed", start, ops or {})


def stock(item, qty):
    return SupplyLot(item, qty, AS_OF, "stock", "WH-MAIN")


def receipt(item, qty, day, ref="PO-1/1"):
    return SupplyLot(item, qty, day, "po", ref)


def req(result, order, item):
    return next(r for r in result.requirements if r.order_number == order and r.item == item)


# ------------------------------------------------------------------ rules

def test_scrap_is_applied_once_and_shortage_rounds_up():
    r = run_mrp(make_input({"FG": [BomLine("C", 2, scrap=0.05)]}, [stock("C", 10)], [demand("SO-1", 6)]))
    c = req(r, "SO-1", "C")
    assert c.gross == pytest.approx(12.6316, rel=1e-4)
    assert c.allocated == pytest.approx(10) and r.shortage_units(c) == 3 and c.status == "short"


def test_on_time_receipt_covers_and_late_receipt_is_flagged():
    inp = make_input({"FG": [BomLine("C", 1)]}, [receipt("C", 5, date(2025, 11, 18))],
                     [demand("SO-1", 5, start=date(2025, 11, 20))])
    r = run_mrp(inp)
    assert req(r, "SO-1", "C").status == "covered" and req(r, "SO-1", "C").ready_date == date(2025, 11, 18)
    late = run_mrp(make_input({"FG": [BomLine("C", 1)]}, [receipt("C", 5, date(2025, 11, 25))], [demand("SO-1", 5)]))
    c = req(late, "SO-1", "C")
    assert c.status == "late" and c.ready_date == date(2025, 11, 25) and c.days_late == 5


def test_need_date_never_before_as_of_and_follows_consuming_operation():
    inp = make_input({"FG": [BomLine("A", 1, operation_ref="OP-050"), BomLine("B", 1, operation_ref="OP-999")]},
                     [stock("A", 1), stock("B", 1)],
                     [demand("SO-1", 1, start=date(2025, 11, 1), ops={"OP-050": date(2025, 11, 24)})])
    r = run_mrp(inp)
    assert req(r, "SO-1", "A").need_date == date(2025, 11, 24)   # scheduled consuming operation
    assert req(r, "SO-1", "B").need_date == AS_OF                 # unscheduled → production start, clamped to as-of


def test_priority_wins_then_earliest_need_and_no_double_allocation():
    inp = make_input({"FG": [BomLine("C", 1)]}, [stock("C", 10)],
                     [demand("SO-LOW", 6, priority="Low", start=date(2025, 11, 16)),
                      demand("SO-CRIT", 6, priority="Critical", start=date(2025, 11, 30))])
    r = run_mrp(inp)
    assert req(r, "SO-CRIT", "C").allocated == pytest.approx(6)    # priority beats the earlier need date
    low = req(r, "SO-LOW", "C")
    assert low.allocated == pytest.approx(4) and r.shortage_units(low) == 2
    assert sum(x.allocated for x in r.requirements if x.item == "C") == pytest.approx(10)   # never more than supply
    alert = next(a for a in build_alerts(r) if a["order_number"] == "SO-LOW")
    assert "SO-CRIT" in alert["description"] and alert["details"]["allocated_to_higher_priority"] == ["SO-CRIT"]


def test_subassembly_nets_own_stock_before_exploding_children():
    items = {"KIT": Item("KIT", "Kit", "manufactured", lead_time_days=2)}
    boms = {"FG": [BomLine("KIT", 1)], "KIT": [BomLine("PART", 3)]}
    r = run_mrp(make_input(boms, [stock("KIT", 2), receipt("PART", 9, date(2025, 11, 18))],
                           [demand("SO-1", 5, start=date(2025, 11, 25))], items=items))
    kit = req(r, "SO-1", "KIT")
    assert kit.allocated == pytest.approx(2) and kit.planned_production == pytest.approx(3)
    part = req(r, "SO-1", "PART")
    assert part.gross == pytest.approx(9) and part.need_date == date(2025, 11, 23)   # kit lead time earlier
    assert kit.status == "covered" and kit.ready_date == date(2025, 11, 20)          # receipt + 2 days build


def test_subassembly_is_short_when_a_child_is_short():
    items = {"KIT": Item("KIT", "Kit", "manufactured")}
    r = run_mrp(make_input({"FG": [BomLine("KIT", 1)], "KIT": [BomLine("PART", 1)]}, [], [demand("SO-1", 1)], items=items))
    assert req(r, "SO-1", "KIT").status == "short" and req(r, "SO-1", "PART").status == "short"
    assert [a["details"]["component"] for a in build_alerts(r) if a["alert_type"] == "material_shortage"] == ["PART"]


def test_missing_bom_is_needs_data_and_produces_no_requirements():
    r = run_mrp(make_input({}, [], [demand("SO-1", 1, item="NOBOM")]))
    assert r.requirements == [] and r.status == "Needs data"
    assert [a["alert_type"] for a in build_alerts(r)] == ["missing_bom"]


# ------------------------------------------------------------------ supply eligibility (from a snapshot)

def _snapshot_with(**changes):
    snap = json.loads(json.dumps(SNAPSHOT))
    for li in snap["purchase_order_items"]:
        if li["product_id"] == changes["product_id"]:
            li.update(changes["values"])
    return snap


def _product_id(code):
    return next(p["id"] for p in SNAPSHOT["products"] if p["product_code"] == code)


def test_quality_hold_stock_is_not_supply_but_is_reported():
    inp = from_snapshot(SNAPSHOT)
    lots = inp.supplies["SR-ST6-10"]
    assert sum(l.quantity for l in lots) == pytest.approx(10)           # 12 received on QC hold are excluded
    assert inp.holds["SR-ST6-10"] == [("WH-DOCK lot HT-9921-A", 12.0)]


def test_cancelled_rejected_and_undated_receipts_are_not_supply():
    studs = _product_id("ST-IN718-M24")
    assert not from_snapshot(_snapshot_with(product_id=studs, values={"status": "Cancelled"})).supplies.get("ST-IN718-M24")
    assert not from_snapshot(_snapshot_with(product_id=studs, values={"quality_status": "Rejected"})).supplies.get("ST-IN718-M24")
    undated = from_snapshot(_snapshot_with(product_id=studs, values={"expected_delivery_date": None}))
    assert not undated.supplies.get("ST-IN718-M24") and undated.undated_receipts[0][1] == "ST-IN718-M24"


# ------------------------------------------------------------------ golden: spec acceptance tests on the demo data

@pytest.fixture(scope="module")
def demo():
    return run_mrp(from_snapshot(SNAPSHOT))


def test_acceptance_2_missing_critical_component(demo):
    """Insufficient stock and no eligible receipt: shortage identified, linked to the order and operation."""
    rings = [r for r in demo.requirements if r.item == "SR-ST6-10" and r.order_number == "SO-1005"]
    short = next(r for r in rings if r.status == "short")
    assert (short.line_number, short.operation_ref, demo.shortage_units(short)) == (1, "OP-030", 3)
    assert demo.line_ready_date("SO-1005", 1) is None              # never assumed available
    alert = next(a for a in build_alerts(demo) if a["alert_type"] == "material_shortage" and a["order_number"] == "SO-1005")
    assert alert["details"]["required_date"] == "2025-11-19" and "Release QC hold" in alert["recommended_action"]
    assert alert["details"]["impact"] == "OP-030 of SO-1005 cannot start"


def test_acceptance_3_late_supplier_receipt(demo):
    """Availability uses the expected receipt date; the delay is measured against the consuming operation."""
    studs = next(r for r in demo.requirements if r.item == "ST-IN718-M24")
    assert (studs.order_number, studs.operation_ref) == ("SO-1012", "OP-050")
    assert (studs.need_date, studs.ready_date, studs.days_late) == (date(2025, 11, 24), date(2025, 11, 25), 1)
    alert = next(a for a in build_alerts(demo) if a["alert_type"] == "late_supplier_receipt" and a["order_number"] == "SO-1012")
    assert alert["details"]["po_number"] == "PO-8815" and alert["details"]["expected_date"] == "2025-11-25"


def test_acceptance_10_competing_orders(demo):
    """Same stock never allocated twice; the affected order is told who received the stock."""
    for item in {r.item for r in demo.requirements}:
        supplied = sum(l.quantity for l in demo.input.supplies.get(item, []))
        assert sum(r.allocated for r in demo.requirements if r.item == item) <= supplied + 1e-6
    actuators = [r for r in demo.requirements if r.item == "ACT-DA-400"]
    assert sum(r.allocated for r in actuators) == pytest.approx(9)       # PO-8821: 6 for SO-1005, 3 for SO-1022
    so1022 = next(a for a in build_alerts(demo) if a["order_number"] == "SO-1022" and a["details"].get("component") == "SR-ST6-10")
    assert so1022["details"]["allocated_to_higher_priority"] == ["SO-1005"]


def test_acceptance_8_missing_engineering_data(demo):
    assert [(i.order_number, i.kind) for i in demo.issues] == [("SO-1021", "missing_bom")]
    assert demo.status == "Needs data"


def test_time_phased_projection_shows_when_balance_goes_negative(demo):
    events = projection(demo, "SR-ST6-10")
    assert events[0]["kind"] == "stock" and events[0]["balance"] == pytest.approx(10)
    assert min(e["balance"] for e in events) < 0


def test_run_payload_is_ready_for_the_database(demo):
    from datetime import datetime, timezone
    payload = run_payload(demo, datetime(2025, 11, 15, tzinfo=timezone.utc), 0.5)
    assert payload["status"] == "Needs data" and payload["input"]["order_lines"] == 13
    assert {a["alert_type"] for a in payload["alerts"]} >= {"material_shortage", "late_supplier_receipt", "quality_hold", "missing_bom"}
    json.dumps(payload)   # serialisable
