import pytest
from fastapi.testclient import TestClient

from app.data import repository as repo
from app.main import app

client = TestClient(app)


@pytest.mark.parametrize("url,expected", [
    ("/dashboard", "Orders at Delivery Risk"),
    ("/workbench", "Version 4.2 [Baseline Active]"),
    ("/workbench?version=v4.3", "Approved OT"),
    ("/orders", "SO-1005"),
    ("/engineering", "Multi-Level BOM Explosion"),
    ("/engineering?product=V-800-FGV-02", "No approved routing"),
    ("/assistant", "Submit Plan for Human Approval"),
])
def test_pages_render(url, expected):
    r = client.get(url)
    assert r.status_code == 200
    assert expected in r.text


def test_root_redirects_to_dashboard():
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert r.headers["location"] == "/dashboard"


def test_orders_table_filters_by_search_and_tab():
    r = client.get("/orders/table", params={"q": "shell"})
    assert "SO-1005" in r.text and "SO-1021" in r.text and "SO-1012" not in r.text
    r = client.get("/orders/table", params={"tab": "in_production"})
    assert "SO-1014" in r.text and "SO-1005" not in r.text
    assert "<html" not in r.text  # fragment, not a full page


def test_at_risk_tab_includes_needs_data_order():
    r = client.get("/orders/table", params={"tab": "at_risk"})
    assert "SO-1021" in r.text and "Needs Data" in r.text


def test_inspector_404_for_unknown_order():
    assert client.get("/orders/SO-9999/inspector").status_code == 404


def test_bom_scrap_simulation_increases_requirements():
    base = client.get("/engineering/V-600-TBV-10/bom").text
    sim = client.get("/engineering/V-600-TBV-10/bom", params={"scrap_delta": 0.05}).text
    assert "Simulating scrap +5%" in sim and "Simulating scrap" not in base


def test_bom_validation_reports_shortages():
    r = client.post("/engineering/V-600-TBV-10/validate")
    assert "SR-ST6-10" in r.text and "ACT-DA-400" in r.text


def test_assistant_reply_uses_planning_data():
    r = client.post("/assistant/message", data={"prompt": "Why is SO-1012 late?"})
    assert "Dec 20" in r.text or "20 Dec" in r.text
    assert "VTL Capacity Overload" in r.text


def test_assistant_refuses_to_approve():
    r = client.post("/assistant/message", data={"prompt": "Please approve v4.3"})
    assert "Approval is reserved for an authorized human approver" in r.text


def test_submit_requires_both_confirmations_server_side():
    r = client.post("/assistant/submit", data={"version": "v4.3", "expedite_confirmed": "true"})
    assert "must be confirmed" in r.text


def test_submit_never_auto_approves():
    before = len(repo.AUDIT_LOG)
    r = client.post("/assistant/submit", data={"version": "v4.3", "notes": "ok",
                                               "expedite_confirmed": "true", "overtime_confirmed": "true"})
    assert "awaiting approver" in r.text
    assert repo.get_version("v4.3").status == "Proposed"
    assert len(repo.AUDIT_LOG) == before + 1


def test_planner_cannot_commit_plan():
    r = client.post("/approvals/APR-1042/decision", data={"decision": "Commit"})
    assert "cannot commit" in r.text
    assert repo.approval_requests()[0].status == "Pending"
