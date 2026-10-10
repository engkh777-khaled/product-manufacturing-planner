"""M05 test gate: Row Level Security per spec §5 role, as real demo users, in rolled-back transactions.

Requires the demo users from `python -m scripts.seed_demo_users`.
"""

import json

import pytest
from psycopg import errors

V800_DRAFT_BOM = """(select bh.id from bom_headers bh join products p on p.id = bh.product_id
                    where p.product_code = 'V-800-FGV-02' and bh.revision = 'Rev A')"""
V600_APPROVED_BOM = """(select bh.id from bom_headers bh join products p on p.id = bh.product_id
                       where p.product_code = 'V-600-TBV-10' and bh.revision = 'Rev C')"""


def uid(cur, email: str) -> str:
    cur.execute("select id from auth.users where email = %s", (email,))
    row = cur.fetchone()
    if row is None:
        pytest.skip("demo users missing — run: python -m scripts.seed_demo_users")
    return str(row[0])


def act_as(cur, email: str | None, user_id: str | None = None) -> None:
    """Switch the transaction to the `authenticated` role with the given user's JWT claims."""
    sub = user_id or uid(cur, email)
    claims = json.dumps({"sub": sub, "role": "authenticated", "email": email or "nobody@example.com"})
    cur.execute("select set_config('request.jwt.claims', %s, true)", (claims,))
    cur.execute("set local role authenticated")


def count(cur, sql: str) -> int:
    cur.execute(sql)
    return cur.fetchone()[0]


def rows(cur, sql: str) -> int:
    cur.execute(sql)
    return cur.rowcount


# ------------------------------------------------------------------ visibility

def test_staff_see_plant_data(db):
    act_as(db, "stores@example.com")
    assert count(db, "select count(*) from sales_orders") == 10
    assert count(db, "select count(*) from inventory_balances") == 39


def test_signed_in_user_without_role_sees_nothing(db):
    act_as(db, None, user_id="00000000-0000-0000-0000-00000000beef")
    assert count(db, "select count(*) from sales_orders") == 0
    assert count(db, "select count(*) from products") == 0


def test_users_see_only_their_own_roles_admin_sees_all(db):
    act_as(db, "planner@example.com")
    assert count(db, "select count(*) from user_roles") == 1
    db.execute("reset role")
    act_as(db, "admin@example.com")
    assert count(db, "select count(*) from user_roles") >= 7


def test_me_returns_identity_and_roles(db):
    act_as(db, "planner@example.com")
    db.execute("select public.me()")
    me = db.fetchone()[0]
    assert me["full_name"] == "Sarah Chen" and me["roles"] == ["planner"]


def test_role_helpers_are_not_callable_by_anon(db):
    db.execute("set local role anon")
    with pytest.raises(errors.InsufficientPrivilege):
        db.execute("select private.has_role('admin')")


# ------------------------------------------------------------------ write permissions by role

def test_planner_edits_orders_but_not_inventory(db):
    act_as(db, "planner@example.com")
    assert rows(db, "update sales_orders set notes = 'rls test' where order_number = 'SO-1005'") == 1
    assert rows(db, "update inventory_balances set on_hand_quantity = 999") == 0


def test_stores_edits_inventory_but_not_orders(db):
    act_as(db, "stores@example.com")
    assert rows(db, "update inventory_balances set reserved_quantity = 0") == 39
    assert rows(db, "update sales_orders set notes = 'rls test'") == 0


def test_planner_cannot_approve_a_schedule(db):
    """Blocked twice since M07: by RLS and by the workflow guard (decide_plan_approval only)."""
    act_as(db, "planner@example.com")
    with pytest.raises(errors.InsufficientPrivilege):
        db.execute("update schedule_versions set status = 'Approved', approved_at = now() where version_number = '4.3'")


# Approver decisions (approve, self-approval, own versions) are tested in test_approvals_db.py (M07 workflow).


def test_engineering_edits_draft_bom_but_approved_bom_is_locked(db):
    act_as(db, "engineering@example.com")
    assert rows(db, f"update bom_items set scrap_factor = 0.03 where bom_header_id = {V800_DRAFT_BOM}") == 2
    assert rows(db, f"update bom_items set scrap_factor = 0.03 where bom_header_id = {V600_APPROVED_BOM}") == 0


def test_engineering_cannot_approve_its_own_bom(db):
    act_as(db, "engineering@example.com")
    with pytest.raises(errors.InsufficientPrivilege):
        db.execute(f"update bom_headers set status = 'Approved', approved_at = now() where id = {V800_DRAFT_BOM}")


def test_approver_can_approve_a_bom_under_review_only(db):
    act_as(db, "engineering@example.com")
    assert rows(db, f"update bom_headers set status = 'Under Review' where id = {V800_DRAFT_BOM}") == 1
    db.execute("reset role")
    act_as(db, "approver@example.com")
    assert rows(db, f"update bom_headers set status = 'Approved', approved_at = now() where id = {V800_DRAFT_BOM}") == 1


def test_approver_cannot_skip_engineering_review(db):
    # M12: a Draft revision must be submitted (Under Review) before it can be approved
    act_as(db, "approver@example.com")
    with pytest.raises(errors.InsufficientPrivilege, match="Only engineering edits revisions"):
        db.execute(f"update bom_headers set status = 'Approved', approved_at = now() where id = {V800_DRAFT_BOM}")


def test_audit_log_is_append_only_and_private(db):
    planner = uid(db, "planner@example.com")
    act_as(db, "planner@example.com")
    db.execute("insert into audit_logs (user_id, action, entity_type) values (%s, 'rls test', 'test')", (planner,))
    assert count(db, "select count(*) from audit_logs") == 0          # planners cannot read audit history
    assert rows(db, "update audit_logs set action = 'tampered'") == 0  # nor change it
    assert rows(db, "delete from audit_logs") == 0
    db.execute("reset role")
    act_as(db, "approver@example.com")
    assert count(db, "select count(*) from audit_logs where action = 'rls test'") == 1


def test_cannot_write_audit_entries_as_someone_else(db):
    approver = uid(db, "approver@example.com")
    act_as(db, "planner@example.com")
    with pytest.raises(errors.InsufficientPrivilege):
        db.execute("insert into audit_logs (user_id, action, entity_type) values (%s, 'forged', 'test')", (approver,))


def test_planner_cannot_grant_roles(db):
    planner = uid(db, "planner@example.com")
    act_as(db, "planner@example.com")
    with pytest.raises(errors.InsufficientPrivilege):
        db.execute("insert into user_roles (user_id, role) values (%s, 'approver')", (planner,))
