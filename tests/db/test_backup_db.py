"""M12 backup and recovery check: the logical export covers every table and detects damage."""

import copy

from scripts.export_data import export, verify


def test_export_covers_every_table_and_verifies(db):
    backup = export(db)
    db.execute("select count(*) from pg_tables where schemaname = 'public'")
    assert len(backup["manifest"]) == db.fetchone()[0]
    db.execute("select count(*) from public.sales_orders")
    assert backup["manifest"]["sales_orders"]["rows"] == db.fetchone()[0]
    assert backup["schema_migration"] and verify(backup, db) == []


def test_damaged_or_incomplete_backups_are_detected(db):
    backup = export(db)
    damaged = copy.deepcopy(backup)
    damaged["tables"]["products"][0]["name"] = "tampered"
    assert any("products: checksum mismatch" in p for p in verify(damaged, db))
    incomplete = copy.deepcopy(backup)
    del incomplete["manifest"]["audit_logs"], incomplete["tables"]["audit_logs"]
    assert any("audit_logs: exists in the database but not in the backup" in p for p in verify(incomplete, db))


def test_migration_files_match_the_database_history(db):
    """The repository's migrations are exactly what the database recorded (no missing or extra migration)."""
    from scripts.setup_database import plan
    p = plan(db)
    assert not p["empty"] and p["pending"] == [] and p["unknown_recorded"] == []
