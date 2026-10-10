"""M06: the Supabase-backed dataset, built offline from a recorded planning_snapshot() (fictional data).

Runs every page on database-shaped data without network access, and checks the derived planning fields.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.data import dataset, supabase_dataset
from app.data import repository as repo
from app.main import app

SNAPSHOT = json.loads((Path(__file__).parent / "fixtures" / "snapshot.json").read_text(encoding="utf-8"))


@pytest.fixture
def db_dataset(monkeypatch):
    built = supabase_dataset.build_dataset(SNAPSHOT)
    # current() falls back to `mock_data` when no request dataset is installed: point that at the snapshot.
    monkeypatch.setattr(dataset, "mock_data", built)
    return built


@pytest.fixture
def client(db_dataset):
    return TestClient(app)


def test_snapshot_converts_to_the_same_models(db_dataset):
    assert db_dataset.SOURCE == "supabase"
    assert len(db_dataset.SALES_ORDERS) == 10
    assert set(db_dataset.SCHEDULE_VERSIONS) == {"v4.2", "v4.3"}
    assert db_dataset.SCHEDULE_VERSIONS["v4.2"].is_baseline


def test_sales_order_planning_fields_are_derived_from_data(db_dataset):
    so1005 = repo.get_order("SO-1005")
    assert so1005["o"].projected_completion.isoformat() == "2025-12-03"   # baseline production order finish
    assert so1005["late"] == 3 and so1005["o"].critical_shortage
    so1021 = repo.get_order("SO-1021")
    assert so1021["o"].readiness == "Needs Data" and so1021["late"] is None   # draft BOM, no routing


def test_multilevel_bom_from_chained_single_level_boms(db_dataset):
    view = repo.bom_view("V-600-TBV-10")
    codes = {r["item"].component_code: r for r in view["rows"]}
    assert codes["PL-A36-12"]["depth"] == 3                       # valve → kit → bracket → plate
    assert codes["SR-ST6-10"]["available"] == 10                  # 12 on QC hold excluded
    # M08: MRP allocates across orders — SO-1022 (Low priority) also lacks bodies/balls after SO-1005 (Critical).
    assert repo.shortage_skus() == ["ACT-DA-400", "SR-ST6-10", "ST-IN718-M24", "TB-316-HC10", "VB-LF2-L10", "VB-LF2-U10"]


def test_capacity_and_purchasing(db_dataset):
    b = repo.bottleneck()
    assert b["wc"].code == "WC-40" and round(b["pct"]) == 96
    pos = {p["po"].po_number: p for p in repo.purchase_orders()}
    assert pos["PO-8821"]["slip"] == 4 and pos["PO-8821"]["po"].status == "Late"
    assert pos["PO-8840"]["po"].status == "QC Hold"


def test_gantt_marks_shortage_blocked_operation_and_material_arrival(db_dataset):
    base = repo.gantt_view("v4.2")
    blocked = [(b["op"].order_number, b["op"].op_code) for b in base["bars"] if b["op"].shortage_blocked]
    assert ("SO-1005", "OP-070") in blocked
    assert [m["m"].at.date().isoformat() for m in base["markers"]] == ["2025-11-28"]
    scenario = repo.gantt_view("v4.3")
    assert [m["m"].at.date().isoformat() for m in scenario["markers"]] == ["2025-11-25"]   # expedite assumption
    assert any(b["b"].kind == "ot" for b in scenario["blocks"])
    assert repo.scenario_comparison()["cand"].key == "v4.3"


@pytest.mark.parametrize("url,expected", [
    ("/dashboard", "SO-1005"),
    ("/workbench", "Version 4.2 [Baseline Active]"),
    ("/workbench?version=v4.3", "Scenario OT"),
    ("/orders?selected=SO-1021", "Needs Data"),
    ("/inventory", "HT-9921-A"),
    ("/engineering?product=V-600-TBV-10", "BOM Revision History"),
    ("/engineering?product=V-800-FGV-02", "No approved routing"),
    ("/assistant", "Submit Plan for Human Approval"),
])
def test_pages_render_from_database_data(client, url, expected):
    r = client.get(url)
    assert r.status_code == 200
    assert expected in r.text


def test_order_editing_requires_planner_role_and_supabase_backend(client, db_dataset):
    # The test user is the AUTH_MODE=disabled demo planner, so the form is available on Supabase data …
    assert client.get("/orders/new").status_code == 200
    # … but without a real session token nothing can be written.
    r = client.post("/orders/save", data={"order_number": "SO-9999", "customer_name": "Test", "order_date": "2025-11-20",
                                          "requested_delivery_date": "2025-12-20", "priority": "Normal",
                                          "product_code": "V-300-FBV-08", "quantity": "2"})
    assert r.status_code == 422 and "requires DATA_BACKEND=supabase and a signed-in user" in r.text


def test_order_form_validation(client):
    r = client.post("/orders/save", data={"order_number": "SO-9999", "customer_name": "", "order_date": "2025-12-20",
                                          "requested_delivery_date": "2025-11-20", "priority": "Normal",
                                          "product_code": "V-800-FGV-02", "quantity": "0"})
    assert r.status_code == 422
    for message in ("Customer is required.", "cannot be before the order date", "choose an active product",
                    "whole number above zero"):
        assert message in r.text


def test_mock_backend_keeps_editing_disabled():
    c = TestClient(app)
    assert c.get("/orders/new").status_code == 403
    assert "Read-only demo data" in c.get("/inventory").text
