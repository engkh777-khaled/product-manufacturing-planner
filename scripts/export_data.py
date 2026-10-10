"""Logical backup of all application data and a recovery check (M12, spec Phase 6 "backup and recovery checks").

    python -m scripts.export_data                       # writes backups/pmp-<project>-<UTC time>.json
    python -m scripts.export_data --verify FILE         # file intact (checksums) and row counts vs the live DB

Complements Supabase's own backups (see development-khaled/operations.md). Uses the owner connection in
DATABASE_URL, so it includes rows that RLS hides from users. Auth users and passwords are NOT exported.
Backups contain business data: keep them out of git (backups/ is ignored) and store them encrypted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
from datetime import datetime, timezone

import psycopg

from app.config import Settings

OUT = pathlib.Path("backups")


def tables(cur) -> list[str]:
    cur.execute("select tablename from pg_tables where schemaname = 'public' order by tablename")
    return [r[0] for r in cur.fetchall()]


def export(cur) -> dict:
    data = {}
    for t in tables(cur):
        cur.execute(f"select coalesce(json_agg(t), '[]'::json) from public.{t} t")
        data[t] = cur.fetchone()[0]
    manifest = {t: {"rows": len(rows), "sha256": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()}
                for t, rows in data.items()}
    cur.execute("select max(version) from supabase_migrations.schema_migrations")
    migration = cur.fetchone()[0]
    return {"format": "pmp-backup-1", "created_at": datetime.now(timezone.utc).isoformat(), "schema_migration": migration,
            "manifest": manifest, "tables": data}


def verify(backup: dict, cur) -> list[str]:
    problems = []
    for t, info in backup["manifest"].items():
        rows = backup["tables"].get(t)
        if rows is None or len(rows) != info["rows"]:
            problems.append(f"{t}: row count does not match the manifest")
        elif hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest() != info["sha256"]:
            problems.append(f"{t}: checksum mismatch (file damaged or edited)")
    live = set(tables(cur))
    for t in sorted(live - set(backup["manifest"])):
        problems.append(f"{t}: exists in the database but not in the backup")
    return problems


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", type=pathlib.Path, help="check a backup file against its manifest and the live DB")
    args = parser.parse_args()
    with psycopg.connect(Settings().database_url.get_secret_value(), sslmode="require") as conn:
        cur = conn.cursor()
        if args.verify:
            backup = json.loads(args.verify.read_text(encoding="utf-8"))
            problems = verify(backup, cur)
            for t, info in backup["manifest"].items():
                cur.execute(f"select count(*) from public.{t}")
                live = cur.fetchone()[0]
                flag = "" if live == info["rows"] else f"   (live now {live})"
                print(f"  {t:<34}{info['rows']:>7}{flag}")
            print("Backup OK" if not problems else "Backup PROBLEMS:\n  " + "\n  ".join(problems))
            return 1 if problems else 0
        backup = export(cur)
    OUT.mkdir(exist_ok=True)
    path = OUT / f"pmp-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps(backup, ensure_ascii=False), encoding="utf-8")
    total = sum(m["rows"] for m in backup["manifest"].values())
    print(f"Exported {len(backup['manifest'])} tables, {total} rows, schema {backup['schema_migration']} → {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
