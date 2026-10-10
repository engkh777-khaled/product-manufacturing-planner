"""M07: Approvals & Audit page and approval logic (offline: demo data and a recorded Supabase snapshot)."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.data import dataset, mock_data, supabase_dataset
from app.data import repository as repo
from app.main import app
from app.routes.approvals import changed_fields

SNAPSHOT = json.loads((Path(__file__).parent / "fixtures" / "snapshot.json").read_text(encoding="utf-8"))


@pytest.fixture
def client():
    return TestClient(app, follow_redirects=False)


@pytest.fixture(autouse=True)
def reset_mock_approvals():
    yield
    for r in mock_data.APPROVAL_REQUESTS:
        r.status, r.requested_by = "Pending", "Sarah Chen"


def test_approvals_page_on_demo_data(client):
    r = client.get("/approvals")
    assert r.status_code == 200
    for text in ("Plans Awaiting Approval", "APR-1042", "Schedule Versions", "Audit Trail", "in-memory app events"):
        assert text in r.text


def test_schedule_changes_list_orders_whose_dates_move():
    cmp = repo.scenario_comparison()
    changes = {c["order"]: c for c in repo.version_changes(cmp["base"], cmp["cand"])}
    assert changes["SO-1005"]["delta_days"] == -4                       # Dec 03 → Nov 29
    assert changes["SO-1005"]["late_before"] and not changes["SO-1005"]["late_after"]
    assert "SO-1008" not in changes                                     # unchanged orders are not listed


def test_approvals_page_on_database_snapshot(monkeypatch, client):
    monkeypatch.setattr(dataset, "mock_data", supabase_dataset.build_dataset(SNAPSHOT))
    r = client.get("/approvals")
    assert r.status_code == 200
    assert "APR-1042" in r.text and "SO-1005" in r.text and "-4 d" in r.text
    # the demo user (AUTH_MODE=disabled) is a planner: RLS-protected audit history is not shown
    assert "visible to approvers and admins only" in r.text


def test_planner_cannot_decide_from_the_page(client):
    # Self-approval is covered in tests/db/test_approvals_db.py and tests/test_auth.py; here: no approver role.
    r = client.post("/approvals/APR-1042/decide", data={"decision": "Approved", "comments": "self"})
    assert r.status_code == 303 and "cannot%20approve" in r.headers["location"]
    assert mock_data.APPROVAL_REQUESTS[0].status == "Pending"


def test_submit_and_withdraw_on_demo_data(client):
    r = client.post("/approvals/submit", data={"version": "v4.3", "notes": "please review"})
    assert r.status_code == 303 and "APR-1042" in r.headers["location"]
    r = client.post("/approvals/APR-1042/withdraw")
    assert "withdrawn" in r.headers["location"]
    assert mock_data.APPROVAL_REQUESTS[0].status == "Withdrawn"


def test_invalid_decision_value_is_rejected(client):
    r = client.post("/approvals/APR-1042/decide", data={"decision": "Maybe"})
    assert "Choose%20Approve%20or%20Reject" in r.headers["location"]


def test_changed_fields_shows_only_real_changes():
    before = {"id": "x", "status": "Proposed", "is_baseline": False, "updated_at": "t1", "label": "v4.3"}
    after = {"id": "x", "status": "Approved", "is_baseline": True, "updated_at": "t2", "label": "v4.3"}
    assert changed_fields(before, after) == ["status: Proposed → Approved", "is_baseline: false → true"]
    assert changed_fields(None, after) == ["created"]
    assert changed_fields(before, None) == ["deleted"]


def test_changed_fields_shows_user_names_instead_of_ids():
    names = {"abbe3de0-0000-0000-0000-000000000000": "Marcus Vance"}
    assert changed_fields({"approved_by": None}, {"approved_by": "abbe3de0-0000-0000-0000-000000000000"}, names) == [
        "approved_by: null → Marcus Vance"]
