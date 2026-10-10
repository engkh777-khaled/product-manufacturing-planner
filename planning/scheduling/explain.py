"""Delivery results and limiting factors per order line (spec §8.7, §9, acceptance test 7).

`earliest_completion` is a lower bound: the line alone on the plant (calendars, confirmed downtime, committed
work, material readiness, precedence, best machine), ignoring competition from other orders. If even that is after
the requested date, the date cannot be achieved under the modeled constraints; otherwise lateness comes from
capacity contention, and the bottleneck machine and the competing orders are named.
"""

from __future__ import annotations

from datetime import timedelta

from planning.scheduling.calendar import SlotGrid
from planning.scheduling.model import Job, LineResult, Schedule


def topo(job: Job) -> list[str]:
    order, seen = [], set()

    def visit(n: str):
        if n in seen:
            return
        seen.add(n)
        for p in job.op(n).predecessors:
            if any(o.number == p for o in job.ops):
                visit(p)
        order.append(n)

    for o in job.ops:
        visit(o.number)
    return order


def earliest_completion(job: Job, grid: SlotGrid, tables) -> tuple[int | None, list[str]]:
    from planning.scheduling.solver import qualified_machines
    inp = tables.inp
    finish, driver, placed = {}, {}, {}
    for n in topo(job):
        if n in job.completed:
            finish[n], driver[n] = grid.ceil(job.completed[n]), "completed"
            continue
        if n in job.fixed:
            finish[n], driver[n] = grid.ceil(job.fixed[n].end), "committed"
            continue
        release = tables.release(job, n)
        preds = [p for p in job.op(n).predecessors if p in finish]
        pred_end = max((finish[p] for p in preds), default=0)
        est = max(release, pred_end)
        if preds and pred_end >= release:
            driver[n] = "predecessor"
        elif job.material_ready.get(n) and release > 0:
            driver[n] = "material"
        else:
            driver[n] = "start"
        best = None
        for code in qualified_machines(inp, job, n):
            ok, end_tab = tables.table(job, n, code)
            s = next((x for x in range(max(est, 0), grid.size) if ok[x]), None)
            if s is not None and (best is None or end_tab[s] < best[0]):
                best = (end_tab[s], s, code, est)
        if best is None:
            return None, [f"{n}: no feasible window on a qualified machine"]
        finish[n], placed[n] = best[0], best
    if not finish:
        return None, []
    # walk the critical chain back from the latest-finishing operation
    sink = max(finish, key=finish.get)
    chain = [sink]
    while driver.get(chain[-1]) == "predecessor":
        preds = [p for p in job.op(chain[-1]).predecessors if p in finish]
        chain.append(max(preds, key=finish.get))
    chain.reverse()
    factors = []
    root = chain[0]
    if driver.get(root) == "material":
        factors.append(f"Material for {root}: {job.material_notes.get(root, 'components')} — "
                       f"ready {job.material_ready[root].astimezone(inp.tz):%b %d}")
    for n in chain:
        if n in placed:
            end, s, code, est = placed[n]
            if any(tables.downtime_only[code][est:end]):
                down = next((d for d in inp.downtime if d.machine == code and d.status == "Confirmed"
                             and grid.floor(d.start) < end and grid.ceil(d.end) > est), None)
                factors.append(f"Confirmed downtime on {code}" + (f" ({down.reason})" if down else ""))
            committed = [i for i in range(max(est, 0), end) if tables.blocked[code][i] and not tables.downtime_only[code][i]]
            if committed:
                holders = sorted({j.key for j in inp.jobs if j.key != job.key for f in j.fixed.values()
                                  if f.machine == code and grid.floor(f.start) < end and grid.ceil(f.end) > est})
                factors.append(f"Committed work on {code}" + (f" ({', '.join(holders)})" if holders else ""))
    hours = sum(job.op(n).work_minutes(job.quantity) for n in chain if n in placed) / 60
    factors.append(f"Processing time: {hours:.1f} working h along {chain[0]}→{chain[-1]} on the plant calendar")
    return finish[sink], factors


def _bottleneck(sched: Schedule, job: Job, grid: SlotGrid, tables) -> str | None:
    """The operation of this line that waited longest (in working time) for its machine, and who held it."""
    ops = {o.op_number: o for o in sched.ops_for(job.key)}
    worst = None
    for n, o in ops.items():
        if o.fixed:
            continue
        ready = tables.release(job, n)
        for p in job.op(n).predecessors:
            if p in ops:
                ready = max(ready, grid.ceil(ops[p].end))
        start = grid.floor(o.start)
        waited = sum(tables.working[o.machine][ready:start])
        if waited > 0 and (worst is None or waited > worst[0]):
            worst = (waited, n, o, ready)
    if not worst:
        return None
    waited, n, o, ready = worst
    window_start = grid.at(ready)
    others = sorted({x.job_key for x in sched.ops if x.machine == o.machine and x.job_key != job.key
                     and x.start < o.start and x.end > window_start})
    hours = waited * grid.slot_minutes / 60
    return f"Capacity: {n} waited {hours:.1f} working h for {o.machine}" + \
           (f" (busy with {', '.join(others)})" if others else "")


def explain_lines(sched: Schedule, grid: SlotGrid, tables) -> list[LineResult]:
    inp = sched.input
    lines = []
    for job in inp.jobs:
        blocked = job.blocked_ops()
        line = LineResult(job.order_number, job.line_number, job.item, job.priority, job.due, "on_time",
                          baseline_finish=job.baseline_finish, blocked_ops=blocked)
        ends = [o.end for o in sched.ops_for(job.key)] + list(job.completed.values())
        if blocked:
            line.status = "blocked"
            roots = [n for n, why in blocked.items() if not why.startswith("after ")]
            line.limiting = [f"{n} cannot start: {blocked[n]} — no eligible supply in the horizon" for n in roots]
            line.achievable = False
        elif ends:
            line.completion = max(ends)
            late_h = (line.completion - job.due).total_seconds() / 3600
            line.lateness_hours = round(max(0.0, late_h), 2)
            bound, factors = earliest_completion(job, grid, tables)
            if bound is not None:
                line.earliest_possible = grid.at(bound)
                line.achievable = line.earliest_possible <= job.due
            if line.lateness_hours > 0:
                line.status = "late"
                if line.achievable is False:
                    line.limiting = [f"Requested date {job.due.astimezone(inp.tz) - timedelta(minutes=1):%b %d} cannot be "
                                     f"achieved under the modeled constraints: earliest possible completion "
                                     f"{line.earliest_possible.astimezone(inp.tz):%b %d %H:%M}"] + factors
                else:
                    neck = _bottleneck(sched, job, grid, tables)
                    line.limiting = [neck] if neck else ["Capacity contention with higher-weighted orders"]
        lines.append(line)
    for issue in inp.issues:
        lines.append(LineResult(issue.order_number, issue.line_number, "", "", inp.as_of, "needs_data",
                                limiting=[issue.detail], achievable=None))
    return lines

