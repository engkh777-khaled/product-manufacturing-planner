"""CP-SAT finite-capacity scheduler (spec §8.3–8.5, §8.7).

Model, in 30-minute slots from as-of:
- one start/end per operation; one optional interval per qualified machine (exactly one is chosen);
- the end follows the machine's calendar: work pauses outside shifts and resumes (element constraint on a
  precomputed table); starting is only allowed where the whole operation avoids confirmed downtime and
  committed (frozen/in-progress) work on that machine;
- precedence from the routing; material readiness from MRP; completed operations release their successors;
- no-overlap per machine;
- objective: priority-weighted lateness and late lines, baseline changes, and operation end times.
"""

from __future__ import annotations

import os
import time as _time
from collections import defaultdict
from datetime import timedelta

from ortools.sat.python import cp_model

from planning.scheduling.calendar import (SlotGrid, blocked_slots, start_pieces, start_table, working_slots,
                                         working_windows)
from planning.scheduling.model import Job, LineResult, Schedule, ScheduledOp, SchedulingInput

ALGORITHM_VERSION = "cpsat-1.0 (calendar-aware intervals, alternative machines, weighted lateness)"


def qualified_machines(inp: SchedulingInput, job: Job, number: str) -> list[str]:
    op = job.op(number)
    return [m.code for m in inp.machines.values()
            if m.active and m.work_center == op.work_center and op.capabilities <= m.capabilities]


def build_grid(inp: SchedulingInput) -> SlotGrid:
    slot = inp.options.slot_minutes
    origin = inp.as_of.replace(second=0, microsecond=0)
    origin -= timedelta(minutes=origin.minute % slot)
    latest = [inp.horizon_end] + [j.due for j in inp.jobs]
    latest += [t for j in inp.jobs for t in j.material_ready.values() if t]
    latest += [f.end for j in inp.jobs for f in j.fixed.values()]
    end = max(latest) + timedelta(days=inp.options.overflow_days)
    return SlotGrid(origin, slot, int((end - origin).total_seconds() // 60 // slot) + 1)


class Tables:
    """Per-machine calendars and start tables shared by the solver and the explanation pass."""

    def __init__(self, inp: SchedulingInput, grid: SlotGrid):
        self.inp, self.grid = inp, grid
        self.working, self.blocked = {}, {}
        end = grid.at(grid.size)
        committed = defaultdict(list)
        for job in inp.jobs:
            for f in job.fixed.values():
                committed[f.machine].append((f.start, f.end))
        for code, machine in inp.machines.items():
            self.working[code] = working_slots(grid, working_windows(inp, machine, grid.origin, end))
            down = [(d.start, d.end) for d in inp.downtime if d.machine == code and d.status == "Confirmed"]
            self.blocked[code] = blocked_slots(grid, down + committed[code])
        self.downtime_only = {code: blocked_slots(grid, [(d.start, d.end) for d in inp.downtime
                                                         if d.machine == code and d.status == "Confirmed"])
                              for code in inp.machines}
        self._cache = {}

    def duration(self, job: Job, number: str, machine: str) -> int:
        return self.grid.slots(job.op(number).work_minutes(job.quantity) / self.inp.machines[machine].factor)

    def table(self, job: Job, number: str, machine: str) -> tuple[list[int], list[int]]:
        d = self.duration(job, number, machine)
        key = (machine, d)
        if key not in self._cache:
            self._cache[key] = start_table(self.working[machine], self.blocked[machine], d)
        return self._cache[key]

    def release(self, job: Job, number: str) -> int:
        """Earliest start slot from as-of, material readiness and completed predecessors."""
        r = 0
        ready = job.material_ready.get(number)
        if ready:
            r = max(r, self.grid.ceil(ready))
        for p in job.op(number).predecessors:
            if p in job.completed:
                r = max(r, self.grid.ceil(job.completed[p]))
        return r


def _to_schedule_ops(inp: SchedulingInput, grid: SlotGrid, tables: Tables, choice: dict) -> list[ScheduledOp]:
    ops = []
    for job in inp.jobs:
        for number, f in job.fixed.items():
            ops.append(ScheduledOp(job.order_number, job.line_number, number, f.machine, f.start, f.end,
                                   job.op(number).work_minutes(job.quantity) / inp.machines[f.machine].factor, fixed=True))
        for number in job.ops:
            key = (job.key, number.number)
            if key in choice:
                machine, s, e = choice[key]
                ops.append(ScheduledOp(job.order_number, job.line_number, number.number, machine, grid.at(s), grid.at(e),
                                       number.work_minutes(job.quantity) / inp.machines[machine].factor))
    return ops


def precheck(inp: SchedulingInput) -> list[str]:
    """Conflicts in committed work that make every schedule infeasible (reported, never silently moved)."""
    conflicts = []
    fixed = [(j, n, f) for j in inp.jobs for n, f in j.fixed.items()]
    for j, n, f in fixed:
        for d in inp.downtime:
            if d.status == "Confirmed" and d.machine == f.machine and d.start < f.end and f.start < d.end:
                conflicts.append(f"Committed {j.key} {n} on {f.machine} overlaps confirmed downtime '{d.reason}' "
                                 f"({d.start:%b %d %H:%M}–{d.end:%b %d %H:%M} UTC)")
    for i, (j1, n1, f1) in enumerate(fixed):
        for j2, n2, f2 in fixed[i + 1:]:
            if f1.machine == f2.machine and f1.start < f2.end and f2.start < f1.end:
                conflicts.append(f"Committed {j1.key} {n1} and {j2.key} {n2} overlap on {f1.machine}")
    return conflicts


def _todo(job: Job) -> list:
    blocked = job.blocked_ops()
    return [o for o in job.ops if o.number not in job.completed and o.number not in job.fixed and o.number not in blocked]


def greedy(inp: SchedulingInput, tables: Tables, pieces: dict) -> dict:
    """Serial list scheduling: lines by priority then due date, operations in routing order, each at its earliest
    finish on any qualified machine. Always feasible; used as the complete CP-SAT hint, to bound the search
    window, and as a proven-feasible fallback when the solver hits its time limit."""
    from planning.scheduling.explain import topo
    busy = defaultdict(list)                     # machine -> [(s, e)]
    placed = {}
    for job in sorted(inp.jobs, key=lambda j: (-j.weight, j.due, j.key)):
        todo = {o.number for o in _todo(job)}
        for n in topo(job):
            if n not in todo:
                continue
            est = 0
            for p in job.op(n).predecessors:
                if (job.key, p) in placed:
                    est = max(est, placed[(job.key, p)][2])
                elif p in job.fixed:
                    est = max(est, tables.grid.ceil(job.fixed[p].end))
            best = None
            for code, ps in pieces[(job.key, n)].items():
                for lo, hi, el in ps:
                    if hi < est:
                        continue
                    s = max(lo, est)
                    while s <= hi:
                        clash = next((b for a, b in busy[code] if a < s + el and s < b), None)
                        if clash is None:
                            break
                        s = clash
                    if s <= hi:
                        if best is None or s + el < best[2]:
                            best = (code, s, s + el)
                        break
            placed[(job.key, n)] = best
            busy[best[0]].append((best[1], best[2]))
    return placed


def solve(inp: SchedulingInput) -> Schedule:
    started = _time.perf_counter()
    opts, w = inp.options, inp.options.weights
    conflicts = precheck(inp)
    grid = build_grid(inp)
    tables = Tables(inp, grid)
    if conflicts:
        return Schedule(inp, "Infeasible", "PRECHECK", None, _time.perf_counter() - started, conflicts=conflicts)

    # 1. Start pieces per operation and qualified machine.
    pieces = {}
    for job in inp.jobs:
        for op in _todo(job):
            release = tables.release(job, op.number)
            ps = {code: start_pieces(*tables.table(job, op.number, code), release)
                  for code in qualified_machines(inp, job, op.number)}
            ps = {code: p for code, p in ps.items() if p}
            if not ps:
                conflicts.append(f"{job.key} {op.number}: no qualified machine has a window without downtime "
                                 f"long enough for {op.work_minutes(job.quantity) / 60:.1f} h of work")
            pieces[(job.key, op.number)] = ps
    if conflicts:
        return Schedule(inp, "Infeasible", "PRECHECK", None, _time.perf_counter() - started, conflicts=conflicts)

    # 2. Greedy schedule; the search window per line ends a few days after max(due date, greedy finish).
    hint = greedy(inp, tables, pieces)
    slack = int(opts.window_days * 24 * 60 / grid.slot_minutes)
    cap = {}
    for job in inp.jobs:
        ends_ = [hint[(job.key, o.number)][2] for o in _todo(job)]
        if ends_:
            cap[job.key] = max(max(ends_), grid.ceil(job.due)) + slack
    for (key, n), ps in pieces.items():
        for code in list(ps):
            ps[code] = [(lo, min(hi, cap[key] - el), el) for lo, hi, el in ps[code] if lo <= cap[key] - el]
            if not ps[code]:
                del ps[code]

    # 3. CP-SAT model.
    m = cp_model.CpModel()
    H = grid.size
    starts, ends, presence = {}, {}, {}
    intervals = defaultdict(list)
    obj = []
    for job in inp.jobs:
        todo = _todo(job)
        for op in todo:
            key = (job.key, op.number)
            ps = pieces[key]
            h_code, h_s, h_e = hint[key]
            spans = sorted([lo, hi] for p in ps.values() for lo, hi, _ in p)
            s = m.NewIntVarFromDomain(cp_model.Domain.FromIntervals(spans), f"s{key}")
            e = m.NewIntVar(0, H, f"e{key}")
            starts[key], ends[key] = s, e
            m.AddHint(s, h_s)
            m.AddHint(e, h_e)
            choices = []
            for code, p in ps.items():
                lits = []
                for lo, hi, el in p:     # start in [lo, hi] on this machine -> finish = start + elapsed
                    b = m.NewBoolVar(f"b{key}{code}{lo}")
                    m.Add(s >= lo).OnlyEnforceIf(b)
                    m.Add(s <= hi).OnlyEnforceIf(b)
                    m.Add(e == s + el).OnlyEnforceIf(b)
                    m.AddHint(b, code == h_code and lo <= h_s <= hi and h_e == h_s + el)
                    lits.append(b)
                choices += lits
                size = m.NewIntVarFromDomain(cp_model.Domain.FromValues(sorted({el for _, _, el in p})), f"sz{key}{code}")
                if len(ps) == 1:
                    intervals[code].append(m.NewIntervalVar(s, size, e, f"i{key}{code}"))
                    presence[(key, code)] = None
                else:
                    pv = m.NewBoolVar(f"p{key}{code}")
                    m.Add(sum(lits) == pv)
                    m.AddHint(pv, code == h_code)
                    intervals[code].append(m.NewOptionalIntervalVar(s, size, e, pv, f"i{key}{code}"))
                    presence[(key, code)] = pv
            m.AddExactlyOne(choices)
            base = job.baseline.get(op.number)
            if base and w.baseline_change:
                bs = max(0, grid.floor(base[1]))
                dev = m.NewIntVar(0, H, f"dev{key}")
                m.AddAbsEquality(dev, s - bs)
                obj.append(w.baseline_change * dev)
            obj.append(w.flow * e)

        for op in todo:                                   # precedence inside the job
            for p in op.predecessors:
                if (job.key, p) in ends:
                    m.Add(starts[(job.key, op.number)] >= ends[(job.key, p)])
                elif p in job.fixed:
                    m.Add(starts[(job.key, op.number)] >= grid.ceil(job.fixed[p].end))
        for number, f in job.fixed.items():               # committed work after a movable predecessor
            for p in job.op(number).predecessors:
                if (job.key, p) in ends:
                    m.Add(ends[(job.key, p)] <= grid.floor(f.start))

        if not job.blocked_ops() and todo:                # lateness (blocked lines cannot complete in this plan)
            sinks = [ends[(job.key, o.number)] for o in todo
                     if not any((job.key, x) in ends for x in job.successors(o.number))]
            c = m.NewIntVar(0, H, f"c{job.key}")
            m.AddMaxEquality(c, sinks + [m.NewConstant(grid.ceil(f.end)) for f in job.fixed.values()])
            due = grid.ceil(job.due)
            late = m.NewIntVar(0, H, f"late{job.key}")
            m.AddMaxEquality(late, [c - due, m.NewConstant(0)])
            is_late = m.NewBoolVar(f"isl{job.key}")
            m.Add(late == 0).OnlyEnforceIf(is_late.Not())
            obj.append(job.weight * (w.lateness * late + w.late_order * is_late))

    for code, ivs in intervals.items():
        if len(ivs) > 1:
            m.AddNoOverlap(ivs)
    m.Minimize(sum(obj) if obj else 0)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = opts.time_limit_s
    solver.parameters.num_workers = opts.workers
    solver.parameters.random_seed = opts.seed
    solver.parameters.log_search_progress = bool(os.environ.get("CPSAT_LOG"))
    solver.parameters.cp_model_probing_level = 0   # light presolve: first solution in ~1.5 s instead of ~4 s
    status = solver.Solve(m)
    name = solver.StatusName(status)
    elapsed = _time.perf_counter() - started

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        choice = {}
        for key, s in starts.items():
            machine = next(code for (k, code), p in presence.items() if k == key and (p is None or solver.Value(p)))
            choice[key] = (machine, solver.Value(s), solver.Value(ends[key]))
        objective = solver.ObjectiveValue()
    elif status == cp_model.UNKNOWN and hint:
        # The greedy schedule is feasible by construction (and is validated independently), so feasibility is
        # proven even though CP-SAT did not improve it within the time limit.
        choice, objective, name = dict(hint), None, "GREEDY (CP-SAT time limit)"
    else:
        outcome = {cp_model.INFEASIBLE: "Infeasible", cp_model.UNKNOWN: "Solver limit reached"}.get(status, "Failed")
        reason = ["No schedule satisfies the hard constraints"] if outcome == "Infeasible" else \
                 [f"Solver stopped after {opts.time_limit_s:g} s without proving feasibility or infeasibility"]
        return Schedule(inp, outcome, name, None, elapsed, conflicts=reason)

    sched = Schedule(inp, "", name, objective, elapsed, ops=_to_schedule_ops(inp, grid, tables, choice))
    from planning.scheduling.explain import explain_lines  # explain reuses the solver's calendar tables
    sched.lines = explain_lines(sched, grid, tables)
    sched.outcome = classify(sched)
    return sched


def classify(sched: Schedule) -> str:
    """Spec §8.7: Needs data wins (the plan is incomplete), then lateness."""
    if sched.input.issues:
        return "Needs data"
    if any(l.status in ("late", "blocked") for l in sched.lines):
        return "Feasible with lateness"
    return "Feasible"


def needs_data_lines(inp: SchedulingInput) -> list[LineResult]:
    return [LineResult(i.order_number, i.line_number, "", "", inp.as_of, "needs_data", limiting=[i.detail])
            for i in inp.issues]
