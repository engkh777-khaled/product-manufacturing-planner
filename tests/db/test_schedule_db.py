"""M09 test gate (database): recording schedule runs, the validation flag guard, invalidation on edits, the
database re-check on submit, and the CLI against the dev project. Every test is rolled back."""

import json
import subprocess
import sys
from datetime import date

import pytest
from psycopg import errors

from planning.scheduling import plan_payload


def scalar(cur, sql, params=None):
    cur.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row else None


@pytest.fixture
def payload(db):
    snap = scalar(db, "select public.planning_snapshot()")
    return plan_payload(snap, ("2025-11-17", "2025-12-15"),
                        {"time_limit_s": 3, "orders": ["SO-1012", "SO-1005", "SO-1014"]})


@pytest.fixture
def recorded(db, act_as, payload):
    act_as("planner@example.com")
    result = scalar(db, "select public.record_schedule_run(%s::jsonb)", (json.dumps(payload),))
    db.execute("reset role")
    return result


def test_planner_records_a_validated_draft_version(db, recorded, payload, user_id):
    assert recorded["validation_passed"] is True and recorded["version_number"].startswith("4.")
    db.execute("""select v.status, v.is_baseline, v.validation_passed, v.created_by::text, r.run_type, r.solver_status,
                         b.version_number
                  from schedule_versions v join planning_runs r on r.id = v.planning_run_id
                  left join schedule_versions b on b.id = v.based_on_version_id
                  where v.version_number = %s""", (recorded["version_number"],))
    assert db.fetchone() == ("Draft", False, True, user_id("planner@example.com"), "schedule",
                             payload["status"], "4.2")
    # production orders and operations are stored per version; machines and routing operations resolved
    stored = scalar(db, """select count(*) from production_operations op join production_orders po on po.id = op.production_order_id
                           join schedule_versions v on v.id = po.schedule_version_id where v.version_number = %s""",
                    (recorded["version_number"],))
    assert stored == sum(len(o["operations"]) for o in payload["orders"])
    assert scalar(db, """select count(*) from production_operations op join production_orders po on po.id = op.production_order_id
                         join schedule_versions v on v.id = po.schedule_version_id
                         where v.version_number = %s and op.planned_start is not null and op.assigned_machine_id is null""",
                  (recorded["version_number"],)) == 0
    # the baseline is untouched (spec §10)
    assert scalar(db, "select version_number from schedule_versions where is_baseline") == "4.2"


def test_only_planners_record_schedule_runs(db, act_as, payload):
    act_as("stores@example.com")
    with pytest.raises(errors.InsufficientPrivilege):
        db.execute("select public.record_schedule_run(%s::jsonb)", (json.dumps(payload),))


def test_no_version_is_stored_without_a_schedule(db, act_as, payload):
    act_as("planner@example.com")
    infeasible = {**payload, "status": "Infeasible", "orders": []}
    result = scalar(db, "select public.record_schedule_run(%s::jsonb)", (json.dumps(infeasible),))
    assert result["version_number"] is None and result["validation_passed"] is False
    assert scalar(db, "select solver_status from planning_runs where run_number = %s", (result["run_number"],)) == "Infeasible"


def test_validation_flag_cannot_be_set_directly(db, act_as, recorded, user_id):
    act_as("planner@example.com")
    db.execute("update schedule_versions set validation_passed = false where version_number = %s",
               (recorded["version_number"],))
    with pytest.raises(errors.InsufficientPrivilege):
        db.execute("update schedule_versions set validation_passed = true where version_number = %s",
                   (recorded["version_number"],))


def test_inserting_a_validated_version_outside_record_schedule_run_is_refused(db, act_as, user_id):
    planner = user_id("planner@example.com")
    act_as("planner@example.com")
    run = scalar(db, """insert into planning_runs (run_type, planning_horizon_start, planning_horizon_end,
                                                   algorithm_version, solver_status, created_by)
                        values ('schedule', '2025-11-17', '2025-12-15', 'forged', 'Feasible', %s) returning id""",
                 (planner,))
    with pytest.raises(errors.InsufficientPrivilege):
        db.execute("""insert into schedule_versions (planning_run_id, version_number, label, status, validation_passed, created_by)
                      values (%s, '9.7', 'forged', 'Draft', true, %s)""", (run, planner))


def test_editing_operations_invalidates_the_validation(db, act_as, recorded):
    act_as("supervisor@example.com")
    db.execute("""update production_operations op set planned_start = op.planned_start + interval '1 hour'
                  from production_orders po, schedule_versions v
                  where po.id = op.production_order_id and v.id = po.schedule_version_id
                    and v.version_number = %s and op.operation_number = 'OP-015'""", (recorded["version_number"],))
    db.execute("reset role")
    db.execute("select validation_passed, metrics ->> 'validation_stale' from schedule_versions where version_number = %s",
               (recorded["version_number"],))
    assert db.fetchone() == (False, "operations edited after validation")


def test_database_recheck_blocks_submission_of_an_overlapping_schedule(db, act_as, recorded):
    number = recorded["version_number"]
    # Corrupt the stored schedule behind the app's back (owner, maintenance mode: the flag stays true).
    db.execute("select set_config('app.audit_maintenance', 'on', true)")
    db.execute("""with ops as (
                    select op.id, op.assigned_machine_id, op.planned_start, op.planned_finish,
                           row_number() over (partition by op.assigned_machine_id order by op.planned_start) n,
                           count(*) over (partition by op.assigned_machine_id) c
                    from production_operations op join production_orders po on po.id = op.production_order_id
                    join schedule_versions v on v.id = po.schedule_version_id
                    where v.version_number = %s and op.planned_start is not null and op.status <> 'Completed')
                  update production_operations p set planned_start = first.planned_start, planned_finish = first.planned_finish
                  from ops first, ops second
                  where first.assigned_machine_id = second.assigned_machine_id and first.n = 1 and second.n = 2
                    and p.id = second.id""", (number,))
    db.execute("select set_config('app.audit_maintenance', '', true)")
    assert scalar(db, "select validation_passed from schedule_versions where version_number = %s", (number,)) is True
    act_as("planner@example.com")
    with pytest.raises(errors.CheckViolation, match="fails the database schedule re-check"):
        db.execute("select public.submit_plan_for_approval(%s, 'forged')", (number,))


def test_validated_draft_can_be_submitted(db, act_as, recorded):
    act_as("planner@example.com")
    request = scalar(db, "select public.submit_plan_for_approval(%s, 'M09 solver run')", (recorded["version_number"],))
    assert request.startswith("APR-")


def test_cli_schedules_the_dev_database(db_conn):
    out = subprocess.run([sys.executable, "-m", "planning.cli", "schedule", "--order", "SO-1012", "--time-limit", "3"],
                         capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert out.returncode == 0, out.stderr[-500:]
    assert "Independent validation: PASSED" in out.stdout and "SO-1012/1" in out.stdout
