"""Set up a Supabase database for this app without the Supabase CLI (reviewers, new environments).

    python -m scripts.setup_database --check     # report only: is the database empty, which migrations are pending
    python -m scripts.setup_database             # EMPTY database: all migrations, then the fictional demo data
    python -m scripts.setup_database --no-seed   # EMPTY database: schema only (never put demo data in production)
    python -m scripts.setup_database --upgrade   # existing database: apply only the migrations not yet recorded

Uses DATABASE_URL from .env (Supabase dashboard → Connect → Session pooler). Everything runs in ONE transaction:
if any statement fails, nothing is changed. Each migration is recorded in supabase_migrations.schema_migrations,
like the Supabase CLI, so `supabase db push` and `scripts/export_data.py` see the same history.
Demo data is only ever loaded into an empty database.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "supabase" / "migrations"
SEED = ROOT / "supabase" / "seed.sql"


def migration_files() -> list[tuple[str, str, Path]]:
    """(version, name, path) in apply order — file names are '<version>_<name>.sql'."""
    out = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        version, _, name = path.stem.partition("_")
        if not version.isdigit() or not name:
            raise SystemExit(f"Unexpected migration file name: {path.name}")
        out.append((version, name, path))
    return out


def database_is_empty(cur) -> bool:
    cur.execute("select to_regclass('public.products') is null and to_regclass('public.sales_orders') is null")
    return cur.fetchone()[0]


def recorded_versions(cur) -> set[str]:
    cur.execute("select to_regclass('supabase_migrations.schema_migrations') is not null")
    if not cur.fetchone()[0]:
        return set()
    cur.execute("select version from supabase_migrations.schema_migrations")
    return {r[0] for r in cur.fetchall()}


def plan(cur) -> dict:
    files = migration_files()
    recorded = recorded_versions(cur)
    return {"empty": database_is_empty(cur), "files": files,
            "pending": [f for f in files if f[0] not in recorded],
            "unknown_recorded": sorted(recorded - {f[0] for f in files})}


def apply(cur, migrations: list[tuple[str, str, Path]], seed: bool, log=print) -> None:
    """Run migrations (and optionally the demo seed) on the given cursor; the caller commits or rolls back."""
    cur.execute("create schema if not exists supabase_migrations")
    cur.execute("create table if not exists supabase_migrations.schema_migrations "
                "(version text primary key, statements text[], name text)")
    for version, name, path in migrations:
        sql = path.read_text(encoding="utf-8")
        cur.execute(sql)
        cur.execute("insert into supabase_migrations.schema_migrations (version, name, statements) values (%s, %s, %s)",
                    (version, name, [sql]))
        log(f"  applied {version}_{name}")
    if seed:
        cur.execute("select set_config('app.audit_maintenance', 'on', false)")   # owner-only: seed is not audited
        cur.execute(SEED.read_text(encoding="utf-8"))
        cur.execute("select set_config('app.audit_maintenance', '', false)")
        log("  loaded fictional demo data (supabase/seed.sql)")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python -m scripts.setup_database")
    parser.add_argument("--check", action="store_true", help="report only, change nothing")
    parser.add_argument("--no-seed", action="store_true", help="schema only (no fictional demo data)")
    parser.add_argument("--upgrade", action="store_true", help="existing database: apply pending migrations only")
    args = parser.parse_args()

    import psycopg

    from app.config import Settings
    settings = Settings()
    if not settings.database_url:
        print("DATABASE_URL is not set in .env (Supabase → Connect → Session pooler connection string).")
        return 1
    with psycopg.connect(settings.database_url.get_secret_value(), sslmode="require", connect_timeout=20) as conn:
        cur = conn.cursor()
        p = plan(cur)
        print(f"Database: {'EMPTY' if p['empty'] else 'already set up'} · {len(p['files'])} migration files · "
              f"{len(p['pending'])} not yet recorded")
        if p["unknown_recorded"]:
            print(f"  note: the database records migrations this code does not have: {', '.join(p['unknown_recorded'])}")
        if args.check:
            for version, name, _ in p["pending"]:
                print(f"  pending {version}_{name}")
            conn.rollback()
            return 0
        if p["empty"]:
            if args.upgrade:
                print("The database is empty: run without --upgrade to set it up.")
                return 1
            print("Setting up an empty database (one transaction):")
            apply(cur, p["files"], seed=not args.no_seed)
        elif args.upgrade:
            if not p["pending"]:
                print("Nothing to do: every migration is already recorded.")
                return 0
            print("Applying pending migrations (one transaction, no demo data):")
            apply(cur, p["pending"], seed=False)
        else:
            print("Refusing: this database already has the app's tables. Use --upgrade to apply new migrations, "
                  "or point DATABASE_URL at an empty project.")
            return 1
        conn.commit()
    print("Done. Next: python -m scripts.seed_demo_users (demo data only) and python -m scripts.set_signing_key")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
