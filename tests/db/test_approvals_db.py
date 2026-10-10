"""M07 test gate: approval workflow, workflow guard and audit trail — in SQL, as real demo users.

Each test runs in one transaction that is rolled back, so nothing persists (sequence numbers excepted).
"""

import pytest
from psycopg import errors


def scalar(cur, sql: str, params=None):
    cur.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row else None


def fails(db, sql: str, params, error) -> None:
    """Assert the statement raises `error`, inside a savepoint so the test transaction stays usable."""
    with pytest.raises(error):
        with db.connection.transaction():
            db.execute(sql, params)


@pytest.fixture
def draft_version(db, user_id):
    """A validated Draft version 9.9 created by the planner (setup is not audited)."""
    planner = user_id("planner@example.com")
    db.execute("select set_config('app.audit_maintenance', 'on', true)")
    db.execute("""insert into planning_runs (run_number, run_type, planning_horizon_start, planning_horizon_end,
                                             algorithm_version, solver_status, created_by)
                  values (9999, 'simulation', '2025-11-15', '2025-12-15', 'pytest', 'Feasible', %s) returning id""", (planner,))
    run = db.fetchone()[0]
    db.execute("""insert into schedule_versions (planning_run_id, version_number, label, status, validation_passed,
                                                 created_by, change_summary)
                  values (%s, '9.9', 'Version 9.9 [pytest]', 'Draft', true, %s, 'pytest scenario')""", (run, planner))
    db.execute("select set_config('app.audit_maintenance', '', true)")
    return "9.9"


def submit_as_planner(db, act_as, comments="please review") -> str:
    act_as("planner@example.com")
    number = scalar(db, "select public.submit_plan_for_approval('9.9', %s)", (comments,))
    db.execute("reset role")
    return number


# ------------------------------------------------------------------ submit

def test_planner_submits_a_validated_plan(db, act_as, user_id, draft_version):
    number = submit_as_planner(db, act_as)
    assert number.startswith("APR-")
    assert scalar(db, "select status from schedule_versions where version_number = '9.9'") == "Proposed"
    db.execute("select status, requested_by::text from approval_requests where request_number = %s", (number,))
    assert db.fetchone() == ("Pending", user_id("planner@example.com"))
    # audit: the request insert and the version status change, both by the planner with the comments as reason
    assert scalar(db, """select count(*) from audit_logs where entity_label = %s and action = 'insert'
                         and reason = 'please review' and user_id = %s""", (number, user_id("planner@example.com"))) == 1
    assert scalar(db, "select count(*) from audit_logs where entity_label = 'v9.9' and action = 'status: Draft → Proposed'") == 1


def test_unvalidated_plan_cannot_be_submitted(db, act_as, draft_version):
    db.execute("update schedule_versions set validation_passed = false where version_number = '9.9'")
    act_as("planner@example.com")
    fails(db, "select public.submit_plan_for_approval('9.9')", None, errors.CheckViolation)


def test_only_planners_submit_and_never_twice(db, act_as, draft_version):
    act_as("stores@example.com")
    fails(db, "select public.submit_plan_for_approval('9.9')", None, errors.InsufficientPrivilege)
    submit_as_planner(db, act_as)
    act_as("planner@example.com")
    fails(db, "select public.submit_plan_for_approval('9.9')", None, errors.CheckViolation)  # already pending


# ------------------------------------------------------------------ decide

def test_planner_cannot_decide(db, act_as, draft_version):
    number = submit_as_planner(db, act_as)
    act_as("planner@example.com")
    fails(db, "select public.decide_plan_approval(%s, 'Approved')", (number,), errors.InsufficientPrivilege)


def test_approval_makes_the_plan_the_baseline_and_supersedes_the_old_one(db, act_as, user_id, draft_version):
    number = submit_as_planner(db, act_as)
    act_as("approver@example.com")
    db.execute("select public.decide_plan_approval(%s, 'Approved', 'go ahead')", (number,))
    db.execute("reset role")
    db.execute("select version_number, status, is_baseline from schedule_versions where version_number in ('4.2', '9.9') order by 1")
    assert db.fetchall() == [("4.2", "Superseded", False), ("9.9", "Approved", True)]
    assert scalar(db, "select count(*) from schedule_versions where is_baseline") == 1
    db.execute("select status, decision_comments, approver_id::text, decided_at is not null from approval_requests where request_number = %s", (number,))
    assert db.fetchone() == ("Approved", "go ahead", user_id("approver@example.com"), True)
    approver = user_id("approver@example.com")
    assert scalar(db, """select count(*) from audit_logs where entity_label = 'v4.2' and action = 'status: Approved → Superseded'
                         and reason = 'go ahead' and user_id = %s""", (approver,)) == 1
    assert scalar(db, "select count(*) from audit_logs where entity_label = 'v9.9' and action = 'status: Proposed → Approved'") == 1


def test_rejection_keeps_the_baseline(db, act_as, draft_version):
    number = submit_as_planner(db, act_as)
    act_as("approver@example.com")
    db.execute("select public.decide_plan_approval(%s, 'Rejected', 'not yet')", (number,))
    db.execute("reset role")
    assert scalar(db, "select status from schedule_versions where version_number = '9.9'") == "Rejected"
    assert scalar(db, "select version_number from schedule_versions where is_baseline") == "4.2"


def test_approver_cannot_decide_own_request(db, act_as, user_id, draft_version):
    number = submit_as_planner(db, act_as)
    db.execute("select set_config('app.audit_maintenance', 'on', true)")
    db.execute("update approval_requests set requested_by = %s where request_number = %s",
               (user_id("approver@example.com"), number))
    db.execute("select set_config('app.audit_maintenance', '', true)")
    act_as("approver@example.com")
    fails(db, "select public.decide_plan_approval(%s, 'Approved')", (number,), errors.InsufficientPrivilege)


def test_approver_cannot_decide_a_version_they_created(db, act_as, user_id, draft_version):
    db.execute("update schedule_versions set created_by = %s where version_number = '9.9'", (user_id("approver@example.com"),))
    number = submit_as_planner(db, act_as)
    act_as("approver@example.com")
    fails(db, "select public.decide_plan_approval(%s, 'Approved')", (number,), errors.InsufficientPrivilege)


def test_withdraw_returns_the_version_to_draft(db, act_as, draft_version):
    number = submit_as_planner(db, act_as)
    act_as("approver@example.com")
    fails(db, "select public.withdraw_plan_approval(%s)", (number,), errors.InsufficientPrivilege)  # not the requester
    act_as("planner@example.com")
    db.execute("select public.withdraw_plan_approval(%s)", (number,))
    db.execute("reset role")
    assert scalar(db, "select status from approval_requests where request_number = %s", (number,)) == "Withdrawn"
    assert scalar(db, "select status from schedule_versions where version_number = '9.9'") == "Draft"


# ------------------------------------------------------------------ guard: no way around the workflow

def test_direct_approval_updates_are_blocked_for_everyone(db, act_as):
    act_as("approver@example.com")
    fails(db, "update schedule_versions set status = 'Approved', approved_at = now() where version_number = '4.3'",
          None, errors.InsufficientPrivilege)
    db.execute("reset role")  # even the database owner (and the service role) must use decide_plan_approval()
    fails(db, "update approval_requests set status = 'Approved', decided_at = now() where request_number = 'APR-1042'",
          None, errors.InsufficientPrivilege)
    fails(db, "update schedule_versions set is_baseline = false where version_number = '4.2'", None, errors.InsufficientPrivilege)


# ------------------------------------------------------------------ audit trail

def test_audit_log_is_append_only_even_for_the_database_owner(db):
    db.execute("insert into audit_logs (action, entity_type, reason) values ('pytest', 'test', 'owner insert') returning id")
    row = db.fetchone()[0]
    fails(db, "update audit_logs set reason = 'tampered' where id = %s", (row,), errors.InsufficientPrivilege)
    fails(db, "delete from audit_logs where id = %s", (row,), errors.InsufficientPrivilege)
    db.execute("select set_config('app.audit_maintenance', 'on', true)")  # explicit owner-only maintenance mode
    db.execute("delete from audit_logs where id = %s", (row,))
    assert db.rowcount == 1


def test_inventory_change_is_audited_with_before_and_after(db, act_as, user_id):
    act_as("stores@example.com")
    db.execute("""update inventory_balances set on_hand_quantity = on_hand_quantity + 5
                  where product_id = (select id from products where product_code = 'GS-PK-010')""")
    db.execute("reset role")
    db.execute("""select entity_label, actor_role, user_id::text, before_data ->> 'on_hand_quantity', after_data ->> 'on_hand_quantity'
                  from audit_logs where entity_type = 'inventory_balances' order by id desc limit 1""")
    label, role, uid, before, after = db.fetchone()
    assert (label, role, uid) == ("GS-PK-010 @ WH-MAIN", "authenticated", user_id("stores@example.com"))
    assert float(after) == float(before) + 5


def test_no_audit_row_when_nothing_changed(db, act_as):
    act_as("planner@example.com")
    db.execute("reset role")
    before = scalar(db, "select count(*) from audit_logs")
    act_as("planner@example.com")
    db.execute("update sales_orders set notes = notes where order_number = 'SO-1005'")
    db.execute("reset role")
    assert scalar(db, "select count(*) from audit_logs") == before


def test_role_grants_are_audited(db, act_as, user_id):
    supervisor = user_id("supervisor@example.com")  # look up as postgres, before switching role
    act_as("admin@example.com")
    db.execute("insert into user_roles (user_id, role) values (%s, 'stores')", (supervisor,))
    db.execute("reset role")
    assert scalar(db, "select count(*) from audit_logs where entity_type = 'user_roles' and action = 'insert' "
                      "and entity_label = 'supervisor@example.com · stores'") == 1
