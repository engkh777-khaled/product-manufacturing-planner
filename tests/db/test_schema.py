"""M04 test gate: schema, constraints, seed data and default-deny RLS."""

import pytest
from psycopg import errors


def scalar(cur, sql: str):
    cur.execute(sql)
    return cur.fetchone()[0]


TABLES = [
    "work_centers", "machines", "shift_calendars", "calendar_exceptions", "machine_downtime",
    "products", "bom_headers", "bom_items", "routing_headers", "routing_operations",
    "routing_operation_dependencies", "warehouses", "inventory_balances", "purchase_orders",
    "purchase_order_items", "sales_orders", "sales_order_items", "planning_runs", "schedule_versions",
    "production_orders", "production_operations", "planning_alerts", "approval_requests", "audit_logs",
]


def test_every_table_exists_with_rls_enabled(db):
    db.execute("""select c.relname, c.relrowsecurity from pg_class c
                  join pg_namespace n on n.oid = c.relnamespace
                  where n.nspname = 'public' and c.relkind = 'r'""")
    rls = dict(db.fetchall())
    missing = [t for t in TABLES if t not in rls]
    assert not missing, f"missing tables: {missing}"
    assert [t for t in TABLES if not rls[t]] == []


@pytest.mark.parametrize("sql,expected", [
    ("select count(*) from work_centers", 7),
    ("select count(*) from machines", 14),
    ("select count(*) from products where item_type = 'finished_good'", 9),
    ("select count(*) from products where item_type <> 'finished_good'", 40),
    ("select count(*) from bom_headers", 12),
    ("select count(*) from bom_items", 46),
    ("select count(*) from routing_operations", 39),
    ("select count(*) from inventory_balances", 39),
    ("select count(*) from purchase_order_items", 5),
    ("select count(*) from sales_orders", 10),
    ("select count(*) from sales_order_items", 13),
    ("select count(*) from schedule_versions where is_baseline", 1),
    ("select count(*) from production_operations", 58),
    ("select count(*) from planning_alerts", 8),
])
def test_seed_counts(db, sql, expected):
    assert scalar(db, sql) == expected


def test_net_available_excludes_quality_hold(db):
    total = scalar(db, """select sum(net_available_quantity) from inventory_balances ib
                          join products p on p.id = ib.product_id where p.product_code = 'SR-ST6-10'""")
    assert total == 10  # 10 in stores + 12 received but on QC hold (PO-8840)


def test_scenario_never_overwrites_baseline(db):
    db.execute("""select sv.version_number, to_char(po.planned_finish at time zone 'America/Chicago', 'YYYY-MM-DD')
                  from production_orders po join schedule_versions sv on sv.id = po.schedule_version_id
                  where po.production_order_number = 'PRD-1005-1'""")
    assert dict(db.fetchall()) == {"4.2": "2025-12-03", "4.3": "2025-11-29"}


def test_needs_data_order_has_no_engineering(db):
    db.execute("""select soi.bom_revision_id, soi.routing_revision_id from sales_order_items soi
                  join sales_orders so on so.id = soi.sales_order_id where so.order_number = 'SO-1021'""")
    assert db.fetchone() == (None, None)


# ------------------------------------------------------------------ constraints

V600_REV_C = """(select bh.id from bom_headers bh join products p on p.id = bh.product_id
                 where p.product_code = 'V-600-TBV-10' and bh.revision = 'Rev C')"""
PRODUCT = "(select id from products where product_code = '{}')"


@pytest.mark.parametrize("name,sql,error", [
    ("duplicate product code",
     "insert into products (product_code, name, item_type) values ('V-600-TBV-10', 'dup', 'finished_good')",
     errors.UniqueViolation),
    ("BOM quantity must be positive",
     f"update bom_items set quantity_per_parent = 0 where bom_header_id = {V600_REV_C} and line_number = 1",
     errors.CheckViolation),
    ("scrap factor below 1",
     f"update bom_items set scrap_factor = 1 where bom_header_id = {V600_REV_C} and line_number = 1",
     errors.CheckViolation),
    ("order status enum",
     "update sales_orders set status = 'Shipped' where order_number = 'SO-1005'",
     errors.CheckViolation),
    ("reserved + hold cannot exceed on hand",
     f"update inventory_balances set reserved_quantity = 11 where lot_number = '' and product_id = {PRODUCT.format('SR-ST6-10')}",
     errors.CheckViolation),
    ("one approved BOM per product",
     f"update bom_headers set status = 'Approved' where revision = 'Rev B' and product_id = {PRODUCT.format('V-600-TBV-10')}",
     errors.UniqueViolation),
    ("BOM self reference",
     f"insert into bom_items (bom_header_id, line_number, component_product_id, quantity_per_parent) "
     f"values ({V600_REV_C}, 9, {PRODUCT.format('V-600-TBV-10')}, 1)",
     errors.CheckViolation),
    ("multilevel BOM cycle (bracket → valve that contains the bracket)",
     f"insert into bom_items (bom_header_id, line_number, component_product_id, quantity_per_parent) "
     f"select bh.id, 9, {PRODUCT.format('V-600-TBV-10')}, 1 from bom_headers bh "
     f"where bh.product_id = {PRODUCT.format('BRK-MNT-A36')}",
     errors.CheckViolation),
    ("BOM revision must belong to the line's product",
     "update sales_order_items set bom_revision_id = (select bh.id from bom_headers bh "
     f"where bh.product_id = {PRODUCT.format('V-300-FBV-08')}) "
     "where sales_order_id = (select id from sales_orders where order_number = 'SO-1022')",
     errors.CheckViolation),
    ("planned line's engineering configuration is frozen",
     "update sales_order_items set routing_revision_id = null "
     "where sales_order_id = (select id from sales_orders where order_number = 'SO-1008')",
     errors.CheckViolation),
    ("requested date not before order date",
     "update sales_orders set requested_delivery_date = '2025-01-01' where order_number = 'SO-1005'",
     errors.CheckViolation),
    ("only one baseline",
     "update schedule_versions set is_baseline = true, status = 'Approved', approved_at = now() where version_number = '4.3'",
     errors.UniqueViolation),
    ("unvalidated schedule cannot be approved",
     "update schedule_versions set validation_passed = false, status = 'Approved', approved_at = now() where version_number = '4.3'",
     errors.CheckViolation),
    ("approval decision needs decided_at",
     "update approval_requests set status = 'Approved' where request_number = 'APR-1042'",
     errors.CheckViolation),
    ("dependencies stay inside one routing",
     "insert into routing_operation_dependencies (operation_id, predecessor_id) "
     "select a.id, b.id from routing_operations a, routing_operations b "
     "where a.routing_header_id <> b.routing_header_id limit 1",
     errors.CheckViolation),
    ("completed operation needs actual times",
     "update production_operations set status = 'Completed' where status = 'Planned' and operation_number = 'OP-020'",
     errors.CheckViolation),
])
def test_constraint_rejects_invalid_data(db, name, sql, error):
    with pytest.raises(error):
        db.execute(sql)


@pytest.mark.parametrize("role,table", [("anon", "products"), ("anon", "sales_orders"),
                                        ("authenticated", "inventory_balances")])
def test_public_roles_see_nothing_until_policies_exist(db, role, table):
    db.execute(f"set local role {role}")
    assert scalar(db, f"select count(*) from public.{table}") == 0
