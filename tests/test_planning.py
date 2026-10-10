from datetime import date

import pytest

from app import planning
from app.data import mock_data as db
from app.models import BomItem, ItemType, RiskLevel


def test_net_available_subtracts_reservations_and_holds():
    assert planning.net_available(10, reserved=3, quality_hold=2) == 5
    assert planning.net_available(2, reserved=5) == 0


def test_required_qty_applies_yield_once():
    # 6 units × 2 per parent / 0.95 yield
    assert planning.required_qty(6, 2, 0.05) == pytest.approx(12.6316, rel=1e-4)
    assert planning.required_qty(1, 1, 0.0) == 1


def test_required_qty_rejects_non_positive_quantities():
    with pytest.raises(ValueError):
        planning.required_qty(1, 0)


@pytest.mark.parametrize("pct,band", [(84.9, "normal"), (85, "constrained"), (92, "constrained"), (92.1, "bottleneck")])
def test_util_band_thresholds(pct, band):
    assert planning.util_band(pct) == band


def test_risk_rules():
    assert planning.risk_level(None) == RiskLevel.UNKNOWN
    assert planning.risk_level(3) == RiskLevel.HIGH
    assert planning.risk_level(-5, critical_shortage=True) == RiskLevel.HIGH
    assert planning.risk_level(-1) == RiskLevel.MEDIUM
    assert planning.risk_level(-4) == RiskLevel.LOW
    assert planning.risk_level(-4, data_complete=False) == RiskLevel.UNKNOWN


def test_lateness_days():
    assert planning.lateness_days(date(2025, 11, 30), date(2025, 12, 3)) == 3
    assert planning.lateness_days(date(2025, 11, 30), None) is None


def test_bom_explosion_propagates_requirements_and_flags_shortages():
    rows = planning.explode_bom(db.BOMS["V-600-TBV-10"].items, order_qty=6)
    by_code = {r["item"].component_code: r for r in rows}
    assert by_code["SR-ST6-10"]["shortage"] == 3          # needs 13, 10 available
    assert by_code["ACT-DA-400"]["shortage"] == 6         # nothing on hand
    assert by_code["VB-LF2-U10"]["shortage"] == 0
    # Level-3 requirement inherits the parent's scrap-adjusted requirement
    bracket = by_code["BRK-MNT-A36"]["net_req"]
    assert by_code["PL-A36-12"]["net_req"] == pytest.approx(bracket * 0.25 / 0.9)
    # Ancestors of a shortage are on the shortage path (for the "Shortages only" filter)
    assert by_code["AMK-TBV-10"]["on_shortage_path"]


def test_bom_explosion_detects_cycles():
    items = [
        BomItem(id="A", parent=None, component_code="A", name="A", item_type=ItemType.ASSY, quantity_per_parent=1),
        BomItem(id="B", parent="C", component_code="B", name="B", item_type=ItemType.MFG, quantity_per_parent=1),
        BomItem(id="C", parent="B", component_code="C", name="C", item_type=ItemType.MFG, quantity_per_parent=1),
    ]
    assert planning.find_cycles(items) == ["B", "C"]
    checks = {c["name"]: c for c in planning.validate_bom(items, "Approved")}
    assert checks["No circular references"]["ok"] is False
    assert planning.find_cycles(db.BOMS["V-600-TBV-10"].items) == []


def test_draft_bom_fails_validation():
    bom = db.BOMS["V-800-FGV-02"]
    checks = {c["name"]: c["ok"] for c in planning.validate_bom(bom.items, bom.status)}
    assert checks["Approved & effective revision"] is False
