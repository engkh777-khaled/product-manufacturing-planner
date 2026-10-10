"""M10 test gate (database): scenarios are stored as separate versions, what-if versions can never be approved,
and a later supplier receipt flags the approved baseline without changing it (acceptance test 9). Rolled back."""

import json
import subprocess
import sys

import pytest

from app.signing import signed_payload
from psycopg import errors

from planning.scenarios import run_scenario
from planning.scheduling import Options

FAST = Options(time_limit_s=3, orders=frozenset({"SO-1005", "SO-1012", "SO-1019"}))


def scalar(cur, sql, params=None):
    cur.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row else None


@pytest.fixture
def snap(db):
    return scalar(db, "select public.planning_snapshot()")


def record(db, act_as, body):
    act_as("planner@example.com")
    result = scalar(db, "select public.record_schedule_run(%s, %s)", signed_payload(body))
    db.execute("reset role")
    return result


def test_9_receipt_change_flags_the_baseline_and_keeps_it(db, act_as):
    alerts_before = scalar(db, "select count(*) from planning_alerts")
    act_as("purchasing@example.com")
    db.execute("""update purchase_order_items li set expected_delivery_date = expected_delivery_date + 5
                  from purchase_orders po where po.id = li.purchase_order_id and po.po_number = 'PO-8821'""")
    db.execute("reset role")
    db.execute("""select severity, description, details ->> 'operation', details ->> 'order_number', details ->> 'baseline_version'
                  from planning_alerts where details ->> 'source' = 'receipt_change' and status = 'Open'
                    and details ->> 'po_number' = 'PO-8821'""")
    rows = db.fetchall()
    assert rows, "a later receipt must flag the baseline"
    severity, description, op, order, version = rows[0]
    # ACT-DA-400 is consumed through the actuator kit AMK-TBV-10 at OP-070 of the SO-1005 valves
    assert (severity, op, order, version) == ("critical", "OP-070", "SO-1005", "4.2")
    assert description.startswith("PO-8821/1 (ACT-DA-400") and "now expected Dec 03" in description
    assert "PRD-1005-1 OP-070 of SO-1005 on Dec 02" in description
    assert scalar(db, "select count(*) from planning_alerts") > alerts_before
    # the approved baseline is preserved and still the only approved version
    assert scalar(db, "select version_number from schedule_versions where is_baseline") == "4.2"
    assert scalar(db, "select count(*) from schedule_versions where status = 'Approved'") == 1


def test_an_earlier_receipt_raises_no_flag(db, act_as):
    act_as("purchasing@example.com")
    db.execute("""update purchase_order_items li set expected_delivery_date = expected_delivery_date - 2
                  from purchase_orders po where po.id = li.purchase_order_id and po.po_number = 'PO-8821'""")
    db.execute("reset role")
    assert scalar(db, "select count(*) from planning_alerts where details ->> 'source' = 'receipt_change' "
                      "and details ->> 'po_number' = 'PO-8821'") == 0


def test_9_what_if_scenario_is_a_separate_version_that_cannot_be_approved(db, act_as, snap):
    body, _, _ = run_scenario(snap, [{"kind": "supplier_delay", "params": {"po_number": "PO-8821", "days": 5}}],
                              "PO-8821 +5 d", options=FAST)
    result = record(db, act_as, body)
    number = result["version_number"]
    db.execute("""select r.run_type, r.scenario_assumptions ->> 'what_if_only', r.scenario_assumptions ->> 'name',
                         v.status, v.is_baseline, b.version_number
                  from schedule_versions v join planning_runs r on r.id = v.planning_run_id
                  join schedule_versions b on b.id = v.based_on_version_id where v.version_number = %s""", (number,))
    assert db.fetchone() == ("simulation", "true", "PO-8821 +5 d", "Draft", False, "4.2")
    assert scalar(db, "select metrics -> 'comparison' -> 'delivery_changes' -> 0 ->> 'key' from schedule_versions "
                      "where version_number = %s", (number,)) == "SO-1005/2"
    assert scalar(db, "select version_number from schedule_versions where is_baseline") == "4.2"
    act_as("planner@example.com")
    with pytest.raises(errors.CheckViolation, match="what-if scenario"):   # aborts the (rolled back) transaction
        db.execute("select public.submit_plan_for_approval(%s)", (number,))


def test_decision_scenario_can_be_submitted_for_approval(db, act_as, snap):
    body, _, _ = run_scenario(snap, [{"kind": "overtime", "params": {"work_center_code": "WC-40",
                                                                     "start": "2025-11-22T06:00", "end": "2025-11-22T14:00"}}],
                              options=FAST)
    result = record(db, act_as, body)
    assert result["validation_passed"] is True
    act_as("planner@example.com")
    assert scalar(db, "select public.submit_plan_for_approval(%s, 'adopt Saturday overtime')",
                  (result["version_number"],)).startswith("APR-")


def test_operations_on_a_hypothetical_machine_are_stored_and_labelled(db, act_as, snap):
    body, _, _ = run_scenario(snap, [{"kind": "extra_machine", "params": {"machine_code": "TE-001"}}], options=FAST)
    # Put one hydro test on the second unit (whether the solver used it or not), keeping its times.
    # (the solver may already have used the second unit; either way one hydro test ends up on TE-001-B)
    row = next(r for o in body["orders"] for r in o["operations"] if r.get("machine_code") in ("TE-001", "TE-001-B"))
    row["machine_code"] = "TE-001-B"
    result = record(db, act_as, body)
    db.execute("""select op.assigned_machine_id, op.notes from production_operations op
                  join production_orders po on po.id = op.production_order_id
                  join schedule_versions v on v.id = po.schedule_version_id
                  where v.version_number = %s and op.notes like 'Scenario machine%%'""", (result["version_number"],))
    machine, notes = db.fetchone()
    assert machine is None and notes.startswith("Scenario machine TE-001-B")
    assert result["validation_passed"] is True            # the DB re-check accepts the labelled scenario machine


def test_unknown_run_type_is_refused(db, act_as, snap):
    act_as("planner@example.com")
    with pytest.raises(errors.CheckViolation):
        db.execute("select public.record_schedule_run(%s, %s)",
                   signed_payload({"run_type": "forecast", "status": "Feasible", "orders": []}))


def test_cli_scenario_is_read_only(db_conn):
    out = subprocess.run([sys.executable, "-m", "planning.cli", "scenario", "--delay", "PO-8821:5", "--time-limit", "3"],
                         capture_output=True, text=True, encoding="utf-8", timeout=240)
    assert out.returncode == 0, out.stderr[-500:]
    assert "SO-1005/2" in out.stdout and "late" in out.stdout and "what-if only" in out.stdout
