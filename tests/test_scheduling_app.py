"""M09 in the app (offline): Resources & Calendars page, solver jobs and the workbench validation panel.
Supabase is replaced by the recorded snapshot and stubbed writes; solver jobs run in a thread (conftest)."""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import jobs
from app.data import dataset, supabase_dataset, writes
from app.main import app
from app.routes import resources, workbench
from planning.scheduling import plan_payload

SNAPSHOT = json.loads((Path(__file__).parent / "fixtures" / "snapshot.json").read_text(encoding="utf-8"))


@pytest.fixture
def db_dataset(monkeypatch):
    built = supabase_dataset.build_dataset(SNAPSHOT)
    monkeypatch.setattr(dataset, "mock_data", built)
    return built


def wait(job, seconds=60):
    end = time.time() + seconds
    while job.status == "running" and time.time() < end:
        time.sleep(0.1)
    return job


# ------------------------------------------------------------------ Resources & Calendars

def test_resources_page_shows_capacity_inputs(db_dataset):
    page = TestClient(app).get("/resources").text
    for text in ("Resources &amp; Calendars", "WC-10", "MC-010", "S1 06:00–14:00 Mon–Fri", "Spindle PM",
                 "Christmas Day", "Plant-wide", "routing operations assigned", "vtl"):
        assert text in page
    assert "Record downtime" not in page                               # planner: read access only


def test_resources_page_works_on_demo_data():
    page = TestClient(app).get("/resources").text
    assert "Read-only demo data without calendars" in page and "WC-10" in page


def test_only_supervisors_record_downtime(db_dataset):
    r = TestClient(app, follow_redirects=False).post("/resources/downtime", data={
        "machine_code": "MC-010", "start": "2025-11-24T06:00", "end": "2025-11-24T10:00", "reason": "test"})
    assert r.status_code == 303 and "supervisor%20or%20admin" in r.headers["location"]


def test_supervisor_records_downtime_in_plant_time(db_dataset, monkeypatch):
    calls = []
    monkeypatch.setattr(resources, "_can_edit", lambda user: True)
    monkeypatch.setattr(writes, "create_downtime",
                        lambda values, code: calls.append((values, code)) or writes.WriteResult(True, "ok"))
    r = TestClient(app, follow_redirects=False).post("/resources/downtime", data={
        "machine_code": "MC-010", "start": "2025-11-24T06:00", "end": "2025-11-24T10:00", "status": "Confirmed",
        "downtime_type": "breakdown", "reason": "Coolant pump"})
    assert r.status_code == 303 and "Re-run%20the%20solver" in r.headers["location"]
    values, code = calls[0]
    assert code == "MC-010" and values["start_datetime"] == "2025-11-24T12:00:00+00:00"   # 06:00 CST = 12:00 UTC
    assert values["status"] == "Confirmed" and values["machine_id"] == next(
        m["id"] for m in SNAPSHOT["machines"] if m["machine_code"] == "MC-010")
    bad = TestClient(app, follow_redirects=False).post("/resources/downtime", data={
        "machine_code": "MC-010", "start": "2025-11-24T10:00", "end": "2025-11-24T06:00", "reason": "x"})
    assert "must%20end%20after" in bad.headers["location"] and len(calls) == 1


def test_unapproved_overtime_stays_unapproved(db_dataset, monkeypatch):
    calls = []
    monkeypatch.setattr(resources, "_can_edit", lambda user: True)
    monkeypatch.setattr(writes, "create_calendar_exception", lambda v: calls.append(v) or writes.WriteResult(True, "ok"))
    TestClient(app, follow_redirects=False).post("/resources/exceptions", data={
        "exception_type": "overtime", "description": "Sat OT", "scope": "WC-10",
        "start": "2025-11-22T06:00", "end": "2025-11-22T14:00"})
    assert calls[0]["is_approved"] is False and "work_center_id" in calls[0]


# ------------------------------------------------------------------ solver jobs

def test_demo_data_run_shows_the_recorded_run_and_never_pretends_to_solve():
    page = TestClient(app).post("/workbench/run").text
    assert "Solving needs DATA_BACKEND=supabase" in page and "hx-trigger" not in page


def test_schedule_job_solves_validates_and_saves_as_the_user(monkeypatch):
    saved = {}
    monkeypatch.setattr(writes, "fetch_snapshot", lambda token: writes.WriteResult(True, "ok", SNAPSHOT))

    def record(body, token=None):
        saved.update(body=body, token=token)
        return writes.WriteResult(True, "Run #1200: saved as Draft v4.4", {"run_number": 1200, "version_number": "4.4",
                                                                          "validation_passed": True})
    monkeypatch.setattr(writes, "record_schedule_run", record)
    job = wait(jobs.start("token-abc", "user-1", "Sarah Chen"))
    assert job.status == "done", job.message
    assert (job.version_number, job.run_number, job.validation_passed) == ("4.4", 1200, True)
    assert saved["token"] == "token-abc" and saved["body"]["status"] == job.outcome == "Feasible with lateness"
    assert saved["body"]["input"]["options"]["time_limit_s"] == 3.0
    assert {l["status"] for l in job.lines} >= {"blocked", "on_time"}
    assert jobs.get(job.id, "someone-else") is None                   # jobs are private to their user


def test_failed_save_is_reported(monkeypatch):
    monkeypatch.setattr(writes, "fetch_snapshot", lambda token: writes.WriteResult(False, "Supabase is unreachable."))
    job = wait(jobs.start("t", "user-2", "x"))
    assert job.status == "failed" and "Supabase is unreachable" in job.message


def test_workbench_run_polls_until_the_draft_is_saved(db_dataset, monkeypatch):
    monkeypatch.setattr(workbench, "_can_schedule", lambda user: True)
    monkeypatch.setattr(writes, "fetch_snapshot", lambda token: writes.WriteResult(True, "ok", SNAPSHOT))
    monkeypatch.setattr(writes, "record_schedule_run", lambda body, token=None: writes.WriteResult(
        True, f"Run #1201: {body['status']} — saved as Draft v4.5, validation passed.",
        {"run_number": 1201, "version_number": "4.5", "validation_passed": True}))
    client = TestClient(app)
    first = client.post("/workbench/run").text
    assert "CP-SAT run in progress" in first and 'hx-trigger="every 1s"' in first
    job_id = first.split('hx-get="/workbench/runs/')[1].split('"')[0]
    page = ""
    for _ in range(600):
        page = client.get(f"/workbench/runs/{job_id}").text
        if "in progress" not in page:
            break
        time.sleep(0.1)
    assert "saved as Draft v4.5" in page and 'href="/workbench?version=v4.5"' in page
    assert "SO-1005/1" in page and "SR-ST6-10 short 3" in page       # blocked line explained
    assert client.get("/workbench/runs/nope").status_code == 404


# ------------------------------------------------------------------ workbench report panel

def test_workbench_shows_delivery_and_validation_of_a_solver_version(monkeypatch):
    snap = copy.deepcopy(SNAPSHOT)
    body = plan_payload(snap, None, {"time_limit_s": 2, "orders": ["SO-1012", "SO-1005"]})
    version = next(v for v in snap["schedule_versions"] if v["version_number"] == "4.3")
    version["metrics"] = body["version"]["metrics"]
    monkeypatch.setattr(dataset, "mock_data", supabase_dataset.build_dataset(snap))
    page = TestClient(app).get("/workbench?version=v4.3").text
    for text in ("Delivery by Order Line", "Independent Validation", "SO-1012", "blocked", "machine overlap",
                 "checked", "SR-ST6-10 short 3"):
        assert text in page
    baseline_page = TestClient(app).get("/workbench?version=v4.2").text
    assert "Delivery by Order Line" not in baseline_page              # seeded versions carry no solver report
