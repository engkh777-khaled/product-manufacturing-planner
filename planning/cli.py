"""Planning engine command line.

    python -m planning.cli mrp                      # problems only (late / short / missing data)
    python -m planning.cli mrp --all                # every requirement
    python -m planning.cli mrp --order SO-1005      # one order
    python -m planning.cli mrp --item SR-ST6-10     # time-phased availability of one item
    python -m planning.cli mrp --save --as planner@example.com   # record run + alerts as that user (RLS applies)

    python -m planning.cli schedule --horizon 2025-11-17:2025-12-15   # CP-SAT schedule + independent validation
    python -m planning.cli schedule --machine MC-014                 # one machine's sequence
    python -m planning.cli schedule --freeze-days 3 --time-limit 30 --save --as planner@example.com

    python -m planning.cli scenario --delay PO-8821:5                 # what-if vs today's data (read-only)
    python -m planning.cli check-data [--strict]                     # plant-data quality report
    python -m planning.cli scenario --down MC-014:2025-11-24T06:00/2025-11-25T22:00 --priority SO-1012:Critical --save

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


def _signed(body: dict) -> tuple[str, str | None]:
    from app.signing import signed_payload  # the CLI runs on the server side and holds the signing key (.env)
    return signed_payload(body)


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
                result = conn.execute("select public.record_schedule_run(%s, %s)", _signed(body)).fetchone()[0]
            version = f"draft version {result['version_number']}" if result["version_number"] else "no version (no schedule)"
            print(f"Saved as planning run #{result['run_number']} by {args.as_user}: {version}, "
                  f"validation {'passed' if result['validation_passed'] else 'not passed'}.")
    return 0 if report.passed else 1


def _scenario_changes(args) -> list[dict]:
    changes = []
    for spec in args.delay or []:
        po, _, days = spec.partition(":")
        changes.append({"kind": "supplier_delay", "params": {"po_number": po, "days": int(days or 5)}})
    for spec in args.down or []:
        machine, _, span = spec.partition(":")
        start, _, end = span.partition("/")
        changes.append({"kind": "machine_unavailable", "params": {"machine_code": machine, "start": start, "end": end}})
    for spec in args.urgent or []:
        product, qty, requested, *prio = spec.split(":")
        changes.append({"kind": "urgent_order", "params": {"product_code": product, "quantity": float(qty),
                                                           "requested_date": requested, "priority": (prio or ["Critical"])[0]}})
    for spec in args.qty or []:
        order, _, pct = spec.partition(":")
        changes.append({"kind": "quantity_change", "params": {"order_number": order, "percent": float(pct)}})
    for spec in args.overtime or []:
        wc, _, span = spec.partition(":")
        start, _, end = span.partition("/")
        changes.append({"kind": "overtime", "params": {"work_center_code": wc, "start": start, "end": end}})
    for machine in args.extra_machine or []:
        changes.append({"kind": "extra_machine", "params": {"machine_code": machine}})
    for spec in args.priority or []:
        order, _, prio = spec.partition(":")
        changes.append({"kind": "priority", "params": {"order_number": order, "priority": prio}})
    return changes


def cmd_scenario(args) -> int:
    from planning.scenarios import ScenarioError, run_scenario
    from planning.scheduling import Options
    changes = _scenario_changes(args)
    with _connect() as conn:
        snap = conn.execute("select public.planning_snapshot()").fetchone()[0]
        try:
            body, scen, applied = run_scenario(snap, changes, args.name or "", None, Options(time_limit_s=args.time_limit))
        except ScenarioError as exc:
            sys.exit(f"Scenario error: {exc}")
        c = body["version"]["metrics"]["comparison"]
        tz = scen.input.tz
        print(f"Scenario: {body['assumptions']['name']}  ({'what-if only' if c['what_if_only'] else 'decision scenario'})")
        for ch in c["changes"]:
            print(f"  · {ch['description']}")
        r, s = c["reference"], c["scenario"]
        print(f"\nToday's data → scenario: late lines {r['late_lines']} → {s['late_lines']}, blocked {r['blocked_lines']} → "
              f"{s['blocked_lines']}, lateness {r['total_lateness_hours']} → {s['total_lateness_hours']} h, overtime "
              f"{r['overtime_hours']} → {s['overtime_hours']} h, bottleneck {c['bottleneck']['reference']} → "
              f"{c['bottleneck']['scenario']}")
        print(f"\nDelivery changes ({len(c['delivery_changes'])}):")
        for l in c["delivery_changes"]:
            when = lambda iso: datetime.fromisoformat(iso).astimezone(tz).strftime("%b %d %H:%M") if iso else "-"  # noqa: E731
            delta = f"{l['delta_hours']:+.1f} h" if l["delta_hours"] is not None else ""
            print(f"  {l['key']:<11}{l['reference_status']:>9} {when(l['reference_completion']):<13}→ {l['scenario_status']:<9}"
                  f"{when(l['scenario_completion']):<13}{delta:>9}  {'; '.join(l['reasons'])}")
        for sh in c["new_shortages"]:
            print(f"  new shortage: {sh['order_number']}/{sh['line_number']} {sh['item']} short {sh['shortage']}")
        for sh in c["resolved_shortages"]:
            print(f"  resolved:     {sh['order_number']}/{sh['line_number']} {sh['item']}")
        print(f"\nScenario validation: {'PASSED' if body['version']['validation_passed'] else 'FAILED'}")
        if args.save:
            with conn.transaction():
                _act_as(conn, args.as_user)
                result = conn.execute("select public.record_schedule_run(%s, %s)", _signed(body)).fetchone()[0]
            print(f"Saved as simulation run #{result['run_number']}: version {result['version_number']} "
                  f"(the approved baseline is unchanged).")
    return 0


def cmd_check_data(args) -> int:
    from planning.data_quality import check, summary
    with _connect() as conn:
        snap = conn.execute("select public.planning_snapshot()").fetchone()[0]
    findings = check(snap)
    s = summary(findings)
    print(f"Data quality: {s['error']} errors, {s['warning']} warnings, {s['info']} notes\n")
    for f in findings:
        print(f"  {f.severity:<8}{f.category:<12}{f.entity:<28}{f.message}")
    return 1 if s["error"] and args.strict else 0


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
    scn = sub.add_parser("scenario", help="what-if scenario compared with today's data (never changes the database)")
    scn.add_argument("--delay", action="append", metavar="PO:DAYS", help="supplier receipt moves, e.g. PO-8821:5 (or :-3)")
    scn.add_argument("--down", action="append", metavar="MACHINE:FROM/TO", help="machine unavailable, plant time")
    scn.add_argument("--urgent", action="append", metavar="PRODUCT:QTY:DATE[:PRIORITY]", help="add an urgent order")
    scn.add_argument("--qty", action="append", metavar="ORDER:PERCENT", help="order quantity change, e.g. SO-1008:20")
    scn.add_argument("--overtime", action="append", metavar="WC:FROM/TO", help="approved overtime for a work center")
    scn.add_argument("--extra-machine", action="append", metavar="MACHINE", help="a second machine like this one")
    scn.add_argument("--priority", action="append", metavar="ORDER:PRIORITY", help="change an order's priority")
    scn.add_argument("--name", help="scenario name")
    scn.add_argument("--time-limit", type=float, default=10.0, help="solver time limit per plan (two plans)")
    scn.add_argument("--save", action="store_true", help="save the scenario as a separate version")
    scn.add_argument("--as", dest="as_user", default="planner@example.com", help="user recorded as running it")
    chk = sub.add_parser("check-data", help="plant-data quality checks (engineering, resources, supply, demand)")
    chk.add_argument("--strict", action="store_true", help="exit 1 when any error is found (for CI / cron)")
    args = parser.parse_args(argv)
    return {"mrp": cmd_mrp, "schedule": cmd_schedule, "scenario": cmd_scenario,
            "check-data": cmd_check_data}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
