"""M12 authorization sweep: who may change which table (spec §5), checked for all seven roles against RLS.

Each case picks one existing row (as owner, so the case is never vacuous) and tries a no-op update
(`set id = id`) as every role; a role "may update" when exactly one row is changed. Everything is rolled back.
The expected sets are the authorization design — a policy change that widens or narrows access fails here.
"""

import pytest
from psycopg import errors

ROLES = ("planner", "supervisor", "stores", "purchasing", "engineering", "approver", "admin")
ALL = set(ROLES)

CASES = [
    # reference data: administrators only
    ("work_centers", "true", {"admin"}),
    ("machines", "true", {"admin"}),
    ("shift_calendars", "true", {"admin"}),
    ("warehouses", "true", {"admin"}),
    # capacity events: production supervisors
    ("machine_downtime", "true", {"admin", "supervisor"}),
    ("calendar_exceptions", "true", {"admin", "supervisor"}),
    # engineering master data
    ("products", "true", {"admin", "engineering"}),
    # Approvers pass RLS on revision headers (approve_bom_revision runs as them); the revision guard trigger then
    # limits them to Under Review → Approved and Approved → Superseded — see the tamper tests below.
    ("bom_headers", "status = 'Draft'", {"admin", "engineering", "approver"}),
    ("bom_items", "bom_header_id in (select id from public.bom_headers where status = 'Draft')", {"admin", "engineering"}),
    ("bom_headers", "status = 'Approved'", {"approver"}),                # RLS: supersede only (guard trigger)
    ("routing_headers", "status = 'Approved'", {"approver"}),
    ("routing_operations", "true", set()),                               # all seeded routings are approved
    # supply
    ("inventory_balances", "true", {"admin", "stores"}),
    ("purchase_orders", "true", {"admin", "purchasing"}),
    ("purchase_order_items", "true", {"admin", "purchasing", "stores"}),  # stores record receipts
    # demand and planning
    ("sales_orders", "true", {"admin", "planner"}),
    ("sales_order_items", "true", {"admin", "planner"}),
    ("planning_runs", "true", {"admin", "planner"}),
    ("production_orders", "true", {"admin", "planner", "supervisor"}),     # supervisors report progress
    ("production_operations", "true", {"admin", "planner", "supervisor"}),
    ("planning_alerts", "true", ALL),                                    # any staff member acknowledges alerts
    ("schedule_versions", "is_baseline", {"approver"}),                   # RLS: supersede via the workflow only
    ("schedule_versions", "version_number = '4.3'", {"admin", "planner", "approver"}),  # Proposed scenario
    ("approval_requests", "status = 'Pending'", {"approver", "planner"}),  # approver decides, requester withdraws
    ("audit_logs", "true", set()),                                       # append-only (no update policy)
]


def owner_count(db, table, where):
    db.execute(f"select count(*) from public.{table} where {where}")
    return db.fetchone()[0]


@pytest.mark.parametrize("table,where,allowed", CASES, ids=[f"{t}[{w[:24]}]" for t, w, _ in CASES])
def test_update_rights_by_role(db, act_as, table, where, allowed):
    assert owner_count(db, table, where) > 0, f"no {table} row matches {where!r} — the case would prove nothing"
    granted = set()
    for role in ROLES:
        db.execute("savepoint matrix")
        try:
            act_as(f"{role}@example.com")
            col = "action" if table == "audit_logs" else "id"           # audit ids are GENERATED ALWAYS
            db.execute(f"update public.{table} set {col} = {col} where id = (select id from public.{table} where {where} "
                       f"order by id limit 1)")
            if db.rowcount == 1:
                granted.add(role)
        except (errors.InsufficientPrivilege, errors.CheckViolation):
            pass
        db.execute("rollback to savepoint matrix")
    assert granted == allowed


def test_profiles_own_row_or_admin(db, act_as, user_id):
    planner = user_id("planner@example.com")
    granted = set()
    for role in ROLES:
        db.execute("savepoint p")
        act_as(f"{role}@example.com")
        db.execute("update public.profiles set id = id where id = %s", (planner,))
        if db.rowcount == 1:
            granted.add(role)
        db.execute("rollback to savepoint p")
    assert granted == {"planner", "admin"}


def test_anonymous_and_roleless_users_see_no_table(db, act_as):
    db.execute("select tablename from pg_tables where schemaname = 'public' order by tablename")
    tables = [r[0] for r in db.fetchall()]
    assert len(tables) >= 24
    db.execute("select count(*) from pg_tables where schemaname = 'public' and not rowsecurity")
    assert db.fetchone()[0] == 0                                         # RLS on every table
    for role_setup in ("anon", "roleless"):
        db.execute("savepoint a")
        if role_setup == "anon":
            db.execute("set local role anon")
        else:
            act_as(sub="00000000-0000-0000-0000-000000000001")            # signed in, no application role
        seen = {}
        for t in tables:
            try:
                db.execute(f"select count(*) from public.{t}")
                seen[t] = db.fetchone()[0]
            except errors.InsufficientPrivilege:
                db.execute("rollback to savepoint a")
                if role_setup == "anon":
                    db.execute("set local role anon")
                else:
                    act_as(sub="00000000-0000-0000-0000-000000000001")
                seen[t] = 0
        db.execute("rollback to savepoint a")
        assert {t: n for t, n in seen.items() if n} == {}, f"{role_setup} can read: {seen}"


# ------------------------------------------------------------------ real changes: decided records are locked (M12)

TAMPER = [
    ("approver", "update public.schedule_versions set label = label || ' (edited)' where is_baseline",
     "read-only"),
    ("approver", "update public.schedule_versions set change_summary = 'edited' where version_number = '4.3'",
     "Approvers decide through the approval workflow"),
    ("approver", "update public.bom_headers set notes = 'edited' where status = 'Draft'",
     "Only engineering edits revisions"),
    ("approver", "update public.bom_headers set notes = 'edited' where status = 'Approved'", "locked"),
    ("approver", "update public.routing_headers set effective_to = effective_from + 400 where status = 'Approved'",
     "locked"),
    ("admin", "update public.bom_headers set notes = 'edited' where status = 'Approved'", None),        # RLS: 0 rows
    ("engineering", "update public.routing_headers set effective_to = effective_from + 400 where status = 'Approved'",
     None),
]


@pytest.mark.parametrize("role,sql,message", TAMPER, ids=[f"{r}:{m or 'rls'}" for r, _, m in TAMPER])
def test_decided_and_approved_records_cannot_be_edited(db, act_as, role, sql, message):
    act_as(f"{role}@example.com")
    if message is None:                       # blocked by RLS: the row is not updatable for this role
        db.execute(sql)
        assert db.rowcount == 0
        return
    with pytest.raises(errors.InsufficientPrivilege, match=message):
        db.execute(sql)
