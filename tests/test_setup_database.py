"""scripts/setup_database.py (offline): migration order, planning, and what apply() sends — with a fake cursor."""

from __future__ import annotations

from scripts.setup_database import SEED, apply, migration_files, plan


class FakeCursor:
    def __init__(self, empty=True, recorded=()):
        self.empty, self.recorded, self.sent, self._row = empty, set(recorded), [], None

    def execute(self, sql, params=None):
        self.sent.append((sql, params))
        if "to_regclass('public.products')" in sql:
            self._row = (self.empty,)
        elif "to_regclass('supabase_migrations.schema_migrations')" in sql:
            self._row = (bool(self.recorded),)
        elif sql.startswith("select version from supabase_migrations"):
            self._rows = [(v,) for v in self.recorded]

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


def test_migrations_are_ordered_and_named():
    files = migration_files()
    versions = [v for v, _, _ in files]
    assert len(files) >= 20 and versions == sorted(versions) and len(set(versions)) == len(versions)
    assert files[0][1] == "helpers" and files[-1][2].suffix == ".sql"


def test_plan_for_empty_and_existing_databases():
    p = plan(FakeCursor(empty=True))
    assert p["empty"] and len(p["pending"]) == len(p["files"])
    first, *rest = migration_files()
    p = plan(FakeCursor(empty=False, recorded=[v for v, _, _ in rest] + ["29990101000000"]))
    assert not p["empty"] and [x[0] for x in p["pending"]] == [first[0]]
    assert p["unknown_recorded"] == ["29990101000000"]


def test_apply_runs_every_migration_records_it_and_seeds_in_maintenance_mode():
    cur = FakeCursor()
    files = migration_files()
    apply(cur, files, seed=True, log=lambda *_: None)
    inserts = [p for sql, p in cur.sent if sql.startswith("insert into supabase_migrations")]
    assert [p[0] for p in inserts] == [v for v, _, _ in files]
    sqls = [sql for sql, _ in cur.sent]
    seed_at = sqls.index(SEED.read_text(encoding="utf-8"))
    assert "app.audit_maintenance', 'on'" in sqls[seed_at - 1] and "app.audit_maintenance', ''" in sqls[seed_at + 1]


def test_apply_without_seed_never_loads_demo_data():
    cur = FakeCursor()
    apply(cur, migration_files()[:2], seed=False, log=lambda *_: None)
    assert SEED.read_text(encoding="utf-8") not in [sql for sql, _ in cur.sent]
