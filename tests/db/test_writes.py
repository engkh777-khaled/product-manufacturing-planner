"""M06 test gate: writes through PostgREST as real demo users (RLS enforced), against the dev project.

Every test restores the data it touched (via a direct postgres connection) so the dev database stays as seeded,
and removes the audit rows its writes produced (M07 triggers) using the owner-only maintenance mode.
Requires DATABASE_URL and DEMO_USER_PASSWORD (python -m scripts.seed_demo_users).
"""

import httpx
import psycopg
import pytest

from app.config import Settings
from app.data import dataset, writes

SETTINGS = Settings()
TEST_ORDER = "SO-9901"


def sign_in(email: str) -> str:
    if not SETTINGS.demo_user_password:
        pytest.skip("DEMO_USER_PASSWORD not set — run: python -m scripts.seed_demo_users")
    r = httpx.post(f"{SETTINGS.supabase_url}/auth/v1/token?grant_type=password",
                   headers={"apikey": SETTINGS.supabase_publishable_key},
                   json={"email": email, "password": SETTINGS.demo_user_password.get_secret_value()}, timeout=20)
    assert r.status_code == 200, f"sign-in failed for {email}"
    return r.json()["access_token"]


@pytest.fixture
def as_user():
    tokens = []

    def _as(email: str):
        tokens.append(dataset.use(None, sign_in(email)))
    yield _as
    for t in reversed(tokens):
        dataset.reset(t)


@pytest.fixture
def pg(db_conn):
    """Autocommit owner connection for verification and cleanup (writes above are committed by PostgREST).

    Maintenance mode: cleanup statements are not audited, and test audit rows may be deleted (owner only).
    """
    with psycopg.connect(SETTINGS.database_url.get_secret_value(), autocommit=True, sslmode="require") as conn:
        cur = conn.cursor()
        cur.execute("select set_config('app.audit_maintenance', 'on', false)")
        yield cur


@pytest.fixture(autouse=True)
def remove_test_audit_rows(pg):
    started = one(pg, "select now()")
    yield
    pg.execute("delete from audit_logs where created_at >= %s", (started,))


def one(cur, sql, params=None):
    cur.execute(sql, params)
    row = cur.fetchone()
    return row[0] if row else None


def test_planner_creates_edits_and_confirms_a_draft_order(as_user, pg):
    as_user("planner@example.com")
    payload = {"order_number": TEST_ORDER, "customer_name": "Test Customer (fictional)", "customer_reference": "T-1",
               "order_date": "2025-11-20", "requested_delivery_date": "2025-12-20", "priority": "Normal", "notes": "",
               "lines": [{"product_code": "V-300-FBV-08", "quantity": "2", "unit_price": "14000", "tags": "T-01", "notes": ""}]}
    try:
        assert writes.save_draft_order(payload).ok
        assert one(pg, "select status from sales_orders where order_number = %s", (TEST_ORDER,)) == "Draft"
        assert one(pg, """select count(*) from sales_order_items i join sales_orders o on o.id = i.sales_order_id
                          where o.order_number = %s and i.bom_revision_id is not null and i.routing_revision_id is not null""",
                   (TEST_ORDER,)) == 1   # approved engineering picked automatically

        payload["lines"].append({"product_code": "V-150-DPC-04", "quantity": "5", "unit_price": "", "tags": "", "notes": ""})
        assert writes.save_draft_order(payload).ok
        assert one(pg, "select count(*) from sales_order_items i join sales_orders o on o.id = i.sales_order_id "
                       "where o.order_number = %s", (TEST_ORDER,)) == 2

        assert writes.confirm_order(TEST_ORDER).ok
        assert one(pg, "select status from sales_orders where order_number = %s", (TEST_ORDER,)) == "Confirmed"
        assert not writes.save_draft_order(payload).ok       # confirmed orders are no longer editable
        assert not writes.delete_draft_order(TEST_ORDER).ok  # … nor deletable
    finally:
        pg.execute("delete from sales_orders where order_number = %s", (TEST_ORDER,))


def test_inactive_product_cannot_be_ordered(as_user, pg):
    as_user("planner@example.com")
    payload = {"order_number": TEST_ORDER, "customer_name": "Test", "order_date": "2025-11-20",
               "requested_delivery_date": "2025-12-20", "priority": "Normal",
               "lines": [{"product_code": "V-800-FGV-02", "quantity": "1"}]}   # Pending Release
    try:
        result = writes.save_draft_order(payload)
        assert not result.ok and "not an active finished good" in result.message
        assert one(pg, "select count(*) from sales_orders where order_number = %s", (TEST_ORDER,)) == 0  # atomic
    finally:
        pg.execute("delete from sales_orders where order_number = %s", (TEST_ORDER,))


def test_only_stores_can_adjust_inventory(as_user, pg):
    balance = one(pg, """select ib.id from inventory_balances ib join products p on p.id = ib.product_id
                         where p.product_code = 'GS-PK-010'""")
    original = one(pg, "select on_hand_quantity from inventory_balances where id = %s", (balance,))
    try:
        as_user("planner@example.com")
        denied = writes.update_inventory(str(balance), 30, 0, 0)
        assert not denied.ok and "permission" in denied.message
        as_user("stores@example.com")
        assert writes.update_inventory(str(balance), 30, 2, 0).ok
        assert one(pg, "select net_available_quantity from inventory_balances where id = %s", (balance,)) == 28
        # M07: the API write was audited by the trigger, as the stores user, with before/after values
        assert one(pg, """select count(*) from audit_logs a join auth.users u on u.id = a.user_id
                          where u.email = 'stores@example.com' and a.entity_id = %s
                            and a.after_data ->> 'on_hand_quantity' = '30.0000'""", (balance,)) == 1
    finally:
        pg.execute("update inventory_balances set on_hand_quantity = %s, reserved_quantity = 0 where id = %s",
                   (original, balance))


def test_purchasing_updates_receipt_dates(as_user, pg):
    line = one(pg, """select li.id from purchase_order_items li join purchase_orders po on po.id = li.purchase_order_id
                      where po.po_number = 'PO-8815'""")
    try:
        as_user("purchasing@example.com")
        assert writes.update_po_line(str(line), {"expected_delivery_date": "2025-11-27", "received_quantity": 0,
                                                 "status": "In Transit", "quality_status": "Not Inspected"}).ok
        assert str(one(pg, "select expected_delivery_date from purchase_order_items where id = %s", (line,))) == "2025-11-27"
        as_user("planner@example.com")
        assert not writes.update_po_line(str(line), {"expected_delivery_date": "2025-12-30"}).ok
    finally:
        pg.execute("update purchase_order_items set expected_delivery_date = '2025-11-25' where id = %s", (line,))


def test_bom_revision_workflow_needs_engineering_then_approver(as_user, pg):
    bom = one(pg, """select bh.id from bom_headers bh join products p on p.id = bh.product_id
                     where p.product_code = 'V-800-FGV-02' and bh.revision = 'Rev A'""")
    try:
        as_user("planner@example.com")
        assert not writes.submit_bom_for_review(str(bom)).ok
        as_user("engineering@example.com")
        assert writes.submit_bom_for_review(str(bom)).ok
        assert not writes.approve_bom(str(bom)).ok            # engineering cannot approve its own revision
        as_user("approver@example.com")
        assert writes.approve_bom(str(bom)).ok
        assert one(pg, "select status from bom_headers where id = %s", (bom,)) == "Approved"
        assert one(pg, "select approved_by is not null from bom_headers where id = %s", (bom,))
    finally:
        pg.execute("update bom_headers set status = 'Draft', approved_at = null, approved_by = null where id = %s", (bom,))


def test_plan_approval_round_trip_through_the_api(as_user, pg):
    planner = one(pg, "select id from auth.users where email = 'planner@example.com'")
    run = one(pg, """insert into planning_runs (run_number, run_type, planning_horizon_start, planning_horizon_end,
                                                algorithm_version, solver_status, created_by)
                     values (9998, 'simulation', '2025-11-15', '2025-12-15', 'pytest', 'Feasible', %s) returning id""",
              (planner,))
    pg.execute("""insert into schedule_versions (planning_run_id, version_number, label, status, validation_passed, created_by)
                  values (%s, '9.8', 'Version 9.8 [api test]', 'Draft', true, %s)""", (run, planner))
    try:
        as_user("planner@example.com")
        submitted = writes.submit_plan("9.8", "api round trip")
        assert submitted.ok and str(submitted.data).startswith("APR-")
        number = submitted.data
        again = writes.submit_plan("9.8")
        assert not again.ok and "already awaiting approval" in again.message
        denied = writes.decide_plan(number, "Approved")
        assert not denied.ok and "Only approvers" in denied.message

        as_user("approver@example.com")
        assert writes.decide_plan(number, "Rejected", "needs another iteration").ok
        assert one(pg, "select status from schedule_versions where version_number = '9.8'") == "Rejected"
        assert one(pg, "select version_number from schedule_versions where is_baseline") == "4.2"  # untouched
        assert one(pg, "select decision_comments from approval_requests where request_number = %s",
                   (number,)) == "needs another iteration"
    finally:
        pg.execute("delete from approval_requests where schedule_version_id in "
                   "(select id from schedule_versions where version_number = '9.8')")
        pg.execute("delete from schedule_versions where version_number = '9.8'")
        pg.execute("delete from planning_runs where run_number = 9998")


def test_audit_entries_are_written_as_the_user(as_user, pg):
    as_user("planner@example.com")
    marker = "pytest audit marker"
    try:
        writes.insert_audit(marker, "test-entity", "")
        assert one(pg, "select count(*) from audit_logs a join auth.users u on u.id = a.user_id "
                       "where a.action = %s and u.email = 'planner@example.com'", (marker,)) == 1
    finally:
        pg.execute("delete from audit_logs where action = %s", (marker,))  # maintenance mode (owner only)
