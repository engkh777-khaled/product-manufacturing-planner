"""Planning engine command line.

    python -m planning.cli mrp                      # problems only (late / short / missing data)
    python -m planning.cli mrp --all                # every requirement
    python -m planning.cli mrp --order SO-1005      # one order
    python -m planning.cli mrp --item SR-ST6-10     # time-phased availability of one item
    python -m planning.cli mrp --save --as planner@example.com   # record run + alerts as that user (RLS applies)

Reads one consistent snapshot through DATABASE_URL (.env). Fictional demo data.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timezone

import psycopg

from planning.mrp import from_snapshot, run_mrp
from planning.mrp.alerts import run_payload
from planning.mrp.engine import projection


def _connect():
    from app.config import Settings  # only the CLI reads app configuration; the engine itself stays pure
    settings = Settings()
    if not settings.database_url:
        sys.exit("DATABASE_URL is not set in .env")
    return psycopg.connect(settings.database_url.get_secret_value(), sslmode="require", connect_timeout=20)


def _print_requirements(result, order: str | None, show_all: bool) -> None:
    rows = [r for r in result.requirements
            if (order is None or r.order_number == order) and (show_all or order or r.status != "covered")]
    print(f"{'order/line':<12}{'lvl':<4}{'item':<14}{'need':<12}{'required':>9}{'alloc':>9}{'short':>7}  {'ready':<11}{'status':<8} supply")
    for r in rows:
        print(f"{r.order_number + '/' + str(r.line_number):<12}{r.level:<4}{r.item:<14}{r.need_date.isoformat():<12}"
              f"{r.gross:>9.2f}{r.allocated:>9.2f}{result.shortage_units(r):>7g}  {str(r.ready_date or '-'):<11}"
              f"{r.status:<8} {', '.join(a.ref for a in r.allocations) or '-'}"
              + (f"  (+ planned production {r.planned_production:.2f})" if r.planned_production else ""))
    if not rows:
        print("  (no requirements match)")


def cmd_mrp(args) -> int:
    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    with _connect() as conn:
        snap = conn.execute("select public.planning_snapshot()").fetchone()[0]
        result = run_mrp(from_snapshot(snap, as_of=date.fromisoformat(args.as_of) if args.as_of else None))
        seconds = time.perf_counter() - t0

        inp = result.input
        print(f"MRP as of {inp.as_of} · horizon {inp.horizon[0]} → {inp.horizon[1]} · {len(inp.demands)} order lines · "
              f"{len(result.requirements)} requirements · outcome: {result.status} ({seconds:.2f}s)\n")
        if args.item:
            print(f"Time-phased availability of {args.item}:")
            for e in projection(result, args.item):
                print(f"  {e['date']}  {e['kind']:<12}{e['ref']:<28}{e['qty']:>+9.2f}  balance {e['balance']:>9.2f}")
        else:
            _print_requirements(result, args.order, args.all)
        for issue in result.issues:
            print(f"! {issue.order_number}/{issue.line_number}: {issue.kind} — {issue.detail}")
        print(f"\nShort: {', '.join(result.short_items()) or 'none'} · late: "
              f"{', '.join(sorted({r.item for r in result.requirements if r.status == 'late'})) or 'none'}")

        if args.save:
            payload = run_payload(result, started, seconds)
            with conn.transaction():
                uid = conn.execute("select id from auth.users where email = %s", (args.as_user,)).fetchone()
                if uid is None:
                    sys.exit(f"Unknown user {args.as_user}")
                claims = json.dumps({"sub": str(uid[0]), "role": "authenticated", "email": args.as_user})
                conn.execute("select set_config('request.jwt.claims', %s, true)", (claims,))
                conn.execute("set local role authenticated")
                number = conn.execute("select public.record_mrp_run(%s::jsonb)", (json.dumps(payload),)).fetchone()[0]
            print(f"Saved as planning run #{number} by {args.as_user} with {len(payload['alerts'])} alerts.")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python -m planning.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    mrp = sub.add_parser("mrp", help="material requirements planning")
    mrp.add_argument("--order", help="show one sales order, e.g. SO-1005")
    mrp.add_argument("--item", help="show the time-phased availability of one item")
    mrp.add_argument("--all", action="store_true", help="show covered requirements too")
    mrp.add_argument("--as-of", help="planning date (default: baseline horizon start)")
    mrp.add_argument("--save", action="store_true", help="record the run and its alerts in the database")
    mrp.add_argument("--as", dest="as_user", default="planner@example.com", help="user recorded as running MRP")
    args = parser.parse_args(argv)
    return cmd_mrp(args)


if __name__ == "__main__":
    raise SystemExit(main())
