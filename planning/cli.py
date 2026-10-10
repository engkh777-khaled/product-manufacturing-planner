"""Planning engine command line.

    python -m planning.cli mrp                      # problems only (late / short / missing data)
    python -m planning.cli mrp --all                # every requirement
    python -m planning.cli mrp --order SO-1005      # one order
    python -m planning.cli mrp --item SR-ST6-10     # time-phased availability of one item
    python -m planning.cli mrp --save --as planner@example.com   # record run + alerts as that user (RLS applies)

    python -m planning.cli schedule --horizon 2025-11-17:2025-12-15   # CP-SAT schedule + independent validation
    python -m planning.cli schedule --machine MC-014                 # one machine's sequence
    python -m planning.cli schedule --freeze-days 3 --time-limit 30 --save --as planner@example.com

Reads one consistent snapshot through DATABASE_URL (.env). Fictional demo data.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone

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
                _act_as(conn, args.as_user)
                number = conn.execute("select public.record_mrp_run(%s::jsonb)", (json.dumps(payload),)).fetchone()[0]
            print(f"Saved as planning run #{number} by {args.as_user} with {len(payload['alerts'])} alerts.")
    return 0


def _act_as(conn, email: str) -> None:
    uid = conn.execute("select id from auth.users where email = %s", (email,)).fetchone()
    if uid is None:
        sys.exit(f"Unknown user {email}")
    claims = json.dumps({"sub": str(uid[0]), "role": "authenticated", "email": email})
    conn.execute("select set_config('request.jwt.claims', %s, true)", (claims,))
    conn.execute("set local role authenticated")


def cmd_schedule(args) -> int:
    from planning.scheduling import Options, payload, plan
    started = datetime.now(timezone.utc)
    horizon = None
    if args.horizon:
        a, b = args.horizon.split(":")
        horizon = (date.fromisoformat(a), date.fromisoformat(b))
    options = Options(time_limit_s=args.time_limit, freeze_days=args.freeze_days, include_drafts=args.include_drafts,
                      orders=frozenset(args.order) if args.order else None)
    with _connect() as conn:
        snap = conn.execute("select public.planning_snapshot()").fetchone()[0]
        sched, report = plan(snap, horizon, options)
        inp, tz = sched.input, sched.input.tz
        print(f"Schedule {inp.as_of.astimezone(tz):%Y-%m-%d} → {inp.horizon_end.astimezone(tz) - timedelta(minutes=1):%Y-%m-%d} · "
              f"{len(inp.jobs)} lines · {sum(len(j.ops) for j in inp.jobs)} operations · outcome: {sched.outcome} "
              f"({sched.solver_status}, {sched.seconds:.1f}s)\n")
        for c in sched.conflicts:
            print(f"! {c}")
        if args.machine:
            for o in sorted((o for o in sched.ops if o.machine == args.machine), key=lambda o: o.start):
                print(f"  {o.start.astimezone(tz):%a %b %d %H:%M} → {o.end.astimezone(tz):%a %b %d %H:%M}  "
                      f"{o.job_key:<11}{o.op_number}{'  (committed)' if o.fixed else ''}")
        else:
            print(f"{'line':<11}{'prio':<9}{'status':<11}{'due':<8}{'completion':<14}{'late h':>7}  {'earliest':<8} why")
            for l in sched.lines:
                done = l.completion.astimezone(tz).strftime("%b %d %H:%M") if l.completion else "-"
                ep = l.earliest_possible.astimezone(tz).strftime("%b %d") if l.earliest_possible else "-"
                due = (l.due.astimezone(tz) - timedelta(minutes=1)).strftime("%b %d") if l.status != "needs_data" else "-"
                print(f"{l.key:<11}{l.priority:<9}{l.status:<11}{due:<8}{done:<14}{l.lateness_hours:>7.1f}  {ep:<8} "
                      f"{'; '.join(l.limiting)}")
        print(f"\nIndependent validation: {'PASSED' if report.passed else 'FAILED'} "
              f"({sum(report.checked.values())} checks)")
        for v in report.violations[:20]:
            print(f"  ✗ [{v.check}] {v.message}")
        for w in report.warnings[:10]:
            print(f"  ! [{w.check}] {w.message}")

        if args.save:
            body = payload(sched, report, started)
            with conn.transaction():
                _act_as(conn, args.as_user)
                result = conn.execute("select public.record_schedule_run(%s::jsonb)", (json.dumps(body),)).fetchone()[0]
            version = f"draft version {result['version_number']}" if result["version_number"] else "no version (no schedule)"
            print(f"Saved as planning run #{result['run_number']} by {args.as_user}: {version}, "
                  f"validation {'passed' if result['validation_passed'] else 'not passed'}.")
    return 0 if report.passed else 1


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
    sch = sub.add_parser("schedule", help="finite-capacity schedule (CP-SAT) with independent validation")
    sch.add_argument("--horizon", help="START:END dates, e.g. 2025-11-17:2025-12-15 (default: baseline horizon)")
    sch.add_argument("--order", action="append", help="only these sales orders (repeatable)")
    sch.add_argument("--machine", help="print one machine's operation sequence")
    sch.add_argument("--time-limit", type=float, default=10.0, help="solver time limit in seconds (default 10)")
    sch.add_argument("--freeze-days", type=int, default=0, help="freeze baseline operations starting within N days")
    sch.add_argument("--include-drafts", action="store_true", help="also schedule draft sales orders")
    sch.add_argument("--save", action="store_true", help="record the run and a Draft schedule version")
    sch.add_argument("--as", dest="as_user", default="planner@example.com", help="user recorded as running the solver")
    args = parser.parse_args(argv)
    return cmd_mrp(args) if args.command == "mrp" else cmd_schedule(args)


if __name__ == "__main__":
    raise SystemExit(main())
