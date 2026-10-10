"""M08 test gate (database): saving MRP runs, and the CLI against the dev project."""

import json
import subprocess
import sys
from datetime import datetime, timezone

import pytest
from psycopg import errors

from planning.mrp import from_snapshot, run_mrp
from planning.mrp.alerts import run_payload


def scalar(cur, sql, params=None):
    cur.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row else None


@pytest.fixture
def payload(db):
    snap = scalar(db, "select public.planning_snapshot()")
    return run_payload(run_mrp(from_snapshot(snap)), datetime.now(timezone.utc), 0.1)


def test_planner_records_mrp_run_and_alerts_are_replaced(db, act_as, user_id, payload):
    open_before = scalar(db, "select count(*) from planning_alerts where status = 'Open' and alert_type = 'material_shortage'")
    assert open_before > 0                                           # seeded alerts from run 1038
    act_as("planner@example.com")
    number = scalar(db, "select public.record_mrp_run(%s::jsonb)", (json.dumps(payload),))
    db.execute("reset role")
    assert number >= 1100
    db.execute("select run_type, solver_status, created_by::text from planning_runs where run_number = %s", (number,))
    assert db.fetchone() == ("mrp", "Needs data", user_id("planner@example.com"))
    # Open material alerts now all belong to the new run; capacity / delivery-risk alerts were left alone.
    assert scalar(db, """select count(*) from planning_alerts a join planning_runs r on r.id = a.planning_run_id
                         where a.status = 'Open' and a.alert_type in ('material_shortage', 'late_supplier_receipt')
                           and r.run_number <> %s""", (number,)) == 0
    assert scalar(db, "select count(*) from planning_alerts where status = 'Open' and alert_type = 'capacity_overload'") == 1
    assert scalar(db, """select count(*) from planning_alerts a join planning_runs r on r.id = a.planning_run_id
                         where r.run_number = %s""", (number,)) == len(payload["alerts"])
    # Alerts are linked to the affected order line (spec §7.4).
    assert scalar(db, """select count(*) from planning_alerts a join planning_runs r on r.id = a.planning_run_id
                         join sales_order_items i on i.id = a.sales_order_item_id join sales_orders o on o.id = i.sales_order_id
                         where r.run_number = %s and o.order_number = 'SO-1012' and a.alert_type = 'late_supplier_receipt'""",
                  (number,)) == 1


def test_only_planners_record_mrp_runs(db, act_as, payload):
    act_as("stores@example.com")
    with pytest.raises(errors.InsufficientPrivilege):
        db.execute("select public.record_mrp_run(%s::jsonb)", (json.dumps(payload),))


def test_cli_reports_problems_from_the_database(db_conn):
    out = subprocess.run([sys.executable, "-m", "planning.cli", "mrp", "--order", "SO-1012"],
                         capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert out.returncode == 0, out.stderr[-500:]
    assert "ST-IN718-M24" in out.stdout and "late" in out.stdout and "PO-8815/1" in out.stdout
