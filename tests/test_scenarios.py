"""M10 test gate: what-if scenarios (spec §10) and acceptance test 9 — offline on the recorded snapshot.

Database-side guarantees (what-if versions cannot be approved, receipt changes flag the baseline) are in
tests/db/test_scenarios_db.py."""

from __future__ import annotations

import copy
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import jobs
from app.data import dataset, supabase_dataset, writes
from app.main import app
from app.routes import scenarios as scenario_routes
from planning.scenarios import ScenarioError, apply, run_scenario
from planning.scheduling import Options

SNAPSHOT = json.loads((Path(__file__).parent / "fixtures" / "snapshot.json").read_text(encoding="utf-8"))
FAST = Options(time_limit_s=3)


@pytest.fixture(scope="module")
def delay():
    """Roadmap example: PO-8821 slips five days."""
    original = copy.deepcopy(SNAPSHOT)
    body, scen, applied = run_scenario(SNAPSHOT, [{"kind": "supplier_delay", "params": {"po_number": "PO-8821", "days": 5}}],
                                       "PO-8821 +5 d", options=FAST)
    assert SNAPSHOT == original                                       # the input snapshot is never modified
    return body, scen, applied


# ------------------------------------------------------------------ acceptance test 9

def test_9_receipt_change_scenario_preserves_the_baseline_and_shows_the_impact(delay):
    body, scen, applied = delay
    c = body["version"]["metrics"]["comparison"]
    assert body["run_type"] == "simulation" and body["assumptions"]["what_if_only"] is True
    assert body["version"]["label"] == "Scenario: PO-8821 +5 d"
    changed = {l["key"]: l for l in c["delivery_changes"]}
    assert list(changed) == ["SO-1005/2"]                               # only the line that waits for the actuators
    line = changed["SO-1005/2"]
    assert (line["reference_status"], line["scenario_status"]) == ("on_time", "late")
    assert line["delta_hours"] == 120.0 and "PO-8821/1 (ACT-DA-400) → Dec 03" in line["reasons"][0]
    assert any("cannot be achieved" in x for x in line["limiting"])
    assert c["reference"]["late_lines"] == 0 and c["scenario"]["late_lines"] == 1
    # the approved baseline's dates are reported next to the plans, never replaced
    assert line["approved_baseline_finish"] is not None
    assert body["version"]["validation_passed"] is True


def test_scenario_plan_only_differs_where_the_change_forces_it(delay):
    _, scen, _ = delay
    # the material-ready gate moved: actuation of SO-1005/2 cannot start before Dec 03 (plant time)
    op70 = next(o for o in scen.ops if o.job_key == "SO-1005/2" and o.op_number == "OP-070")
    assert op70.start.astimezone(scen.input.tz).date().isoformat() == "2025-12-03"


# ------------------------------------------------------------------ the other spec §10 examples

def test_urgent_order_takes_capacity_and_material_and_says_so():
    body, scen, applied = run_scenario(SNAPSHOT, [{"kind": "urgent_order", "params": {
        "product_code": "V-300-CGV-04", "quantity": 6, "requested_date": "2025-11-21", "priority": "Critical"}}], options=FAST)
    c = body["version"]["metrics"]["comparison"]
    changed = {l["key"]: l for l in c["delivery_changes"]}
    assert changed["SIM-1/1"]["reference_status"] == "new" and changed["SIM-1/1"]["scenario_status"] == "on_time"
    assert changed["SO-1019/1"]["scenario_status"] == "blocked"        # its components now go to the Critical order
    assert {s["order_number"] for s in c["new_shortages"]} == {"SO-1019"}
    assert all("Urgent Critical order SIM-1" in l["reasons"][0] for l in c["delivery_changes"])
    assert any(o["production_order_number"] == "PRD-SIM-1-1" for o in body["orders"])


def test_machine_unavailable_is_respected_and_decision_scenarios_can_be_adopted():
    changes = [{"kind": "machine_unavailable", "params": {"machine_code": "MC-014", "start": "2025-11-17T06:00",
                                                          "end": "2025-11-20T22:00"}},
               {"kind": "overtime", "params": {"work_center_code": "WC-10", "start": "2025-11-22T06:00",
                                               "end": "2025-11-22T14:00"}},
               {"kind": "priority", "params": {"order_number": "SO-1012", "priority": "Critical"}}]
    body, scen, applied = run_scenario(SNAPSHOT, changes, options=FAST)
    down_start = datetime(2025, 11, 17, 12, tzinfo=timezone.utc)
    down_end = datetime(2025, 11, 21, 4, tzinfo=timezone.utc)
    assert not [o for o in scen.ops if o.machine == "MC-014" and o.start < down_end and down_start < o.end]
    assert body["assumptions"]["what_if_only"] is False                # overtime / outage / priority are decisions
    c = body["version"]["metrics"]["comparison"]
    wc10 = next(u for u in c["utilisation"] if u["work_center"] == "WC-10")
    assert wc10["scenario_pct"] != wc10["reference_pct"]               # capacity changed (outage, overtime)
    assert body["version"]["validation_passed"] is True


def test_quantity_change_and_extra_machine():
    applied = apply(SNAPSHOT, [{"kind": "quantity_change", "params": {"order_number": "SO-1008", "percent": 20}},
                               {"kind": "extra_machine", "params": {"machine_code": "TE-001"}}])
    so1008 = next(o for o in applied.snapshot["sales_orders"] if o["order_number"] == "SO-1008")
    line = next(i for i in applied.snapshot["sales_order_items"] if i["sales_order_id"] == so1008["id"])
    assert line["quantity"] == 15.0                                     # 12 × 1.2 = 14.4 → 15 whole valves
    assert any(m["machine_code"] == "TE-001-B" for m in applied.snapshot["machines"])
    assert applied.what_if_only and "12 → 15" in applied.changes[0].description


@pytest.mark.parametrize("change,message", [
    ({"kind": "supplier_delay", "params": {"po_number": "PO-0000", "days": 5}}, "PO-0000 not found"),
    ({"kind": "machine_unavailable", "params": {"machine_code": "MC-014", "start": "2025-11-20T06:00",
                                                 "end": "2025-11-19T06:00"}}, "must end after"),
    ({"kind": "urgent_order", "params": {"product_code": "V-800-FGV-02", "quantity": 1, "requested_date": "2025-12-01"}},
     "no approved BOM and routing"),
    ({"kind": "priority", "params": {"order_number": "SO-1012", "priority": "Urgent!"}}, "Priority must be"),
    ({"kind": "teleport", "params": {}}, "Unknown scenario change"),
])
def test_invalid_changes_are_explained(change, message):
    with pytest.raises(ScenarioError, match=message):
        apply(SNAPSHOT, [change])


# ------------------------------------------------------------------ app

@pytest.fixture
def db_dataset(monkeypatch, delay):
    """The recorded snapshot plus the delay scenario stored as version 4.9 and a baseline-impact flag."""
    snap = copy.deepcopy(SNAPSHOT)
    body, _, _ = delay
    run = {**snap["planning_runs"][0], "id": "run-sim", "run_number": 1199, "run_type": "simulation",
           "scenario_assumptions": body["assumptions"], "solver_status": body["status"], "solve_seconds": body["seconds"]}
    snap["planning_runs"].append(run)
    snap["schedule_versions"].append({**snap["schedule_versions"][1], "id": "ver-sim", "planning_run_id": "run-sim",
                                      "version_number": "4.9", "label": body["version"]["label"], "status": "Draft",
                                      "is_baseline": False, "metrics": body["version"]["metrics"],
                                      "change_summary": body["version"]["change_summary"], "validation_passed": True})
    # one operation of the scenario on a hypothetical second machine
    po = next(p for p in snap["production_orders"] if p["schedule_version_id"] == snap["schedule_versions"][0]["id"])
    snap["production_orders"].append({**po, "id": "po-sim", "schedule_version_id": "ver-sim"})
    op = next(o for o in snap["production_operations"] if o["production_order_id"] == po["id"])
    snap["production_operations"].append({**op, "id": "op-sim", "production_order_id": "po-sim",
                                          "assigned_machine_id": None, "notes": "Scenario machine TE-001-B"})
    snap["planning_alerts"].append({"id": "al-1", "alert_type": "late_supplier_receipt", "severity": "critical",
                                    "status": "Open", "created_at": "2026-10-10T10:00:00+00:00",
                                    "description": "PO-8821/1 (ACT-DA-400) now expected Dec 03 — approved baseline v4.2 starts PRD-1005-1 OP-070 of SO-1005 on Dec 02 06:00",
                                    "details": {"source": "receipt_change", "po_number": "PO-8821"},
                                    "planning_run_id": None, "sales_order_item_id": None, "production_order_id": None,
                                    "recommended_action": ""})
    built = supabase_dataset.build_dataset(snap)
    monkeypatch.setattr(dataset, "mock_data", built)
    return built


def test_scenarios_page_lists_saved_scenarios_and_the_baseline_flag(db_dataset):
    page = TestClient(app).get("/scenarios").text
    for text in ("Build a Scenario", "PO-8821", "MC-014", "V-600-TBV-10", "v4.9", "what-if only",
                 "Baseline v4.2 is affected by 1 supplier receipt change", "now expected Dec 03"):
        assert text in page


def test_scenario_comparison_page(db_dataset):
    page = TestClient(app).get("/scenarios/v4.9").text
    for text in ("what-if only — not for approval", "Orders Whose Delivery Changes", "SO-1005/2", "+120.0 h",
                 "PO-8821/1 (ACT-DA-400)", "Utilisation by Work Center", "No change in material shortages"):
        assert text in page
    assert TestClient(app).get("/scenarios/v4.2").status_code == 404     # the baseline is not a scenario


def test_workbench_compares_baseline_with_the_chosen_version_from_data(db_dataset):
    page = TestClient(app).get("/workbench?version=v4.9&compare=v4.9").text
    assert "Comparison: Baseline v4.2 vs" in page and 'value="v4.9" selected' in page
    assert "Open scenario comparison" in page and "TE-001-B (scenario)" in page  # hypothetical machine lane
    assert "Approved baseline affected by 1 supplier receipt change" in page
    seeded = TestClient(app).get("/workbench?compare=v4.3").text
    assert "recovers SO-1005 (Shell Offshore GOM)" not in seeded                 # no more hard-coded story
    assert "Earlier:" in seeded or "Later:" in seeded


def test_running_a_scenario_needs_a_planner_session(db_dataset):
    page = TestClient(app).post("/scenarios/run", data={"delay_po": "PO-8821", "delay_days": "5"}).text
    assert "requires DATA_BACKEND=supabase" in page


def test_scenario_form_validation(db_dataset, monkeypatch):
    monkeypatch.setattr(scenario_routes, "_can_run", lambda user: True)
    client = TestClient(app)
    assert "Add at least one change" in client.post("/scenarios/run", data={}).text
    assert "Fill in the start" in client.post("/scenarios/run", data={"down_machine": "MC-014", "down_end": "2025-11-24T10:00"}).text


def test_scenario_job_runs_in_the_background_and_saves_a_version(db_dataset, monkeypatch):
    monkeypatch.setattr(scenario_routes, "_can_run", lambda user: True)
    monkeypatch.setattr(writes, "fetch_snapshot", lambda token: writes.WriteResult(True, "ok", SNAPSHOT))
    saved = {}

    def record(body, token=None):
        saved["body"] = body
        return writes.WriteResult(True, "Scenario run #1300: saved as v4.10 (what-if only, not for approval).",
                                  {"run_number": 1300, "version_number": "4.10", "validation_passed": True})
    monkeypatch.setattr(writes, "record_schedule_run", record)
    client = TestClient(app)
    first = client.post("/scenarios/run", data={"delay_po": "PO-8821", "delay_days": "5", "name": "slip"}).text
    assert "Scenario run in progress" in first
    job_id = first.split('hx-get="/scenarios/runs/')[1].split('"')[0]
    page = ""
    for _ in range(900):
        page = client.get(f"/scenarios/runs/{job_id}").text
        if "in progress" not in page:
            break
        time.sleep(0.1)
    assert "saved as v4.10" in page and 'href="/scenarios/v4.10"' in page and "SO-1005/2" in page
    assert saved["body"]["run_type"] == "simulation" and saved["body"]["assumptions"]["name"] == "slip"


def test_scenario_errors_are_reported_not_crashed(monkeypatch):
    monkeypatch.setattr(writes, "fetch_snapshot", lambda token: writes.WriteResult(True, "ok", SNAPSHOT))
    job = jobs.start("t", "user-9", "x", kind="scenario",
                     changes=[{"kind": "supplier_delay", "params": {"po_number": "PO-0000", "days": 5}}])
    for _ in range(300):
        if job.status != "running":
            break
        time.sleep(0.05)
    assert job.status == "failed" and "PO-0000 not found" in job.message
