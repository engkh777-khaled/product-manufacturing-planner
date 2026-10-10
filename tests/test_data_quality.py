"""M12 plant-data quality checks (fictional data): the recorded snapshot and deliberately broken copies."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.data import dataset, supabase_dataset
from app.main import app
from planning.data_quality import check, summary

SNAPSHOT = json.loads((Path(__file__).parent / "fixtures" / "snapshot.json").read_text(encoding="utf-8"))


def messages(snap):
    return {(f.severity, f.category, f.entity): f.message for f in check(snap)}


def test_recorded_plant_findings():
    found = messages(SNAPSHOT)
    assert ("error", "engineering", "SO-1021/1") in found                   # acceptance test 8 data, seen early
    assert summary(check(SNAPSHOT)) == {"error": 2, "warning": 1, "info": 2}


@pytest.mark.parametrize("breakage,expected", [
    (lambda s: s["purchase_order_items"][0].update(expected_delivery_date=None, status="Open", open_quantity=5),
     ("error", "supply", "no expected date")),
    (lambda s: s["inventory_balances"][0].update(on_hand_quantity=1, reserved_quantity=2, quality_hold_quantity=0),
     ("error", "inventory", "impossible balance")),
    (lambda s: s.update(shift_calendars=[x for x in s["shift_calendars"]
                                          if x["work_center_id"] != next(w["id"] for w in s["work_centers"] if w["work_center_code"] == "WC-40")]),
     ("error", "calendar", "no shift calendar")),
    (lambda s: [m.update(is_active=False) for m in s["machines"] if m["machine_code"] == "VT-001"],
     ("error", "resources", "no active machine in WC-14")),
    (lambda s: s["sales_order_items"][0].update(quantity=0), ("error", "demand", "quantity must be positive")),
])
def test_broken_data_is_reported(breakage, expected):
    snap = copy.deepcopy(SNAPSHOT)
    breakage(snap)
    severity, category, text = expected
    assert any(f.severity == severity and f.category == category and text in f.message for f in check(snap))


def test_resources_page_shows_data_quality(monkeypatch):
    monkeypatch.setattr(dataset, "mock_data", supabase_dataset.build_dataset(copy.deepcopy(SNAPSHOT)))
    page = TestClient(app).get("/resources").text
    assert "Plant Data Quality" in page and "2 errors" in page and "V-800-FGV-02 has no approved BOM" in page


def test_demo_data_has_no_quality_panel():
    assert "Plant Data Quality" not in TestClient(app).get("/resources").text
