"""M08: MRP inside the app (offline: recorded snapshot / demo data)."""

import json
from datetime import date
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
    monkeypatch.setattr(dataset, "mock_data", built)
    return built


def test_orders_get_material_ready_dates_and_mrp_staging(db_dataset):
    so1012 = repo.get_order("SO-1012")["o"]
    assert so1012.material_ready == date(2025, 11, 25)                 # studs PO-8815, the latest component
    so1005 = repo.get_order("SO-1005")["o"]
    assert so1005.material_ready is None                               # seat rings short → not material-ready
    rings = next(s for s in so1005.staging if s.name.startswith("Seat Ring"))
    assert (rings.required, rings.note, rings.blocked_by) == (13, "Short 3", "QC hold")


def test_inventory_page_shows_mrp(db_dataset):
    page = TestClient(app).get("/inventory").text
    for text in ("Material Requirements (MRP)", "ST-IN718-M24", "+1d", "Time-phased availability", "SO-1021/1"):
        assert text in page


def test_running_mrp_needs_a_session_token(db_dataset):
    # AUTH_MODE=disabled demo planner: allowed by role, but there is no Supabase session to write with.
    r = TestClient(app, follow_redirects=False).post("/inventory/mrp/run")
    assert r.status_code == 303 and "requires%20DATA_BACKEND%3Dsupabase%20and%20a%20signed-in%20user" in r.headers["location"]


def test_demo_data_explains_that_mrp_needs_the_database():
    client = TestClient(app, follow_redirects=False)
    assert "The MRP engine runs on database snapshots" in client.get("/inventory").text
    assert "planner%20or%20admin" in client.post("/inventory/mrp/run").headers["location"]
