"""Independent schedule validation (spec §8.8, §8.3 rule 10).

Deliberately shares no code with the solver: durations are recomputed from routing times, and working time is
re-derived from the raw shift / exception records by sampling the calendar every few minutes, instead of using
the solver's slot tables. A schedule with any violation must not be proposed or approved.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from planning.scheduling.model import Job, Machine, Schedule, ScheduledOp, SchedulingInput

STEP = timedelta(minutes=5)          # calendar sampling step
TOLERANCE_MIN = 5.0                  # one sampling step
CHECKS = ("assignments", "inspections", "precedence", "machine_overlap", "downtime", "calendar",
          "work_center_capacity", "material", "frozen", "delivery", "completeness")


@dataclass(frozen=True)
class Violation:
    check: str
    message: str
    ref: str = ""


@dataclass
class ValidationReport:
    violations: list[Violation] = field(default_factory=list)
    warnings: list[Violation] = field(default_factory=list)
    checked: dict[str, int] = field(default_factory=lambda: {c: 0 for c in CHECKS})

    @property
    def passed(self) -> bool:
        return not self.violations

    def by_check(self) -> dict[str, int]:
        counts = {c: 0 for c in CHECKS}
        for v in self.violations:
            counts[v.check] += 1
        return counts

    def as_dict(self) -> dict:
        return {"passed": self.passed, "checked": self.checked, "violations_by_check": self.by_check(),
                "violations": [vars(v) for v in self.violations[:50]],
                "warnings": [vars(w) for w in self.warnings[:50]]}


class _Calendar:
    def __init__(self, inp: SchedulingInput):
        self.inp = inp

    def is_working(self, machine: Machine, t: datetime) -> bool:
        inp = self.inp
        for e in inp.exceptions:
            if e.kind == "overtime" and e.approved and e.applies_to(machine) and e.start <= t < e.end:
                return True
        for e in inp.exceptions:
            if e.kind in ("holiday", "shutdown") and e.applies_to(machine) and e.start <= t < e.end:
                return False
        local = t.astimezone(inp.tz)
        for shift in inp.shifts_for(machine):
            for back in (0, 1):
                d = local.date() - timedelta(days=back)
                if d.isoweekday() not in shift.days:
                    continue
                if (shift.valid_from and d < shift.valid_from) or (shift.valid_to and d > shift.valid_to):
                    continue
                begin = datetime.combine(d, shift.start, inp.tz)
                end = datetime.combine(d + timedelta(days=1) if shift.end <= shift.start else d, shift.end, inp.tz)
                if begin <= local < end:
                    return True
        return False

    def working_minutes(self, machine: Machine, start: datetime, end: datetime) -> float:
        n, t = 0, start + STEP / 2
        while t < end:
            n += self.is_working(machine, t)
            t += STEP
        return n * STEP.total_seconds() / 60


def required_minutes(job: Job, op_number: str, machine: Machine) -> float:
    op = next(o for o in job.ops if o.number == op_number)
    if op.basis == "per_batch":
        batches = math.ceil(job.quantity / op.max_batch - 1e-9) if op.max_batch else 1
        run = batches * op.cycle_min
    else:
        run = job.quantity * op.cycle_min
    return (op.setup_min + run + op.inspection_min) / machine.factor


def _overlap(a: ScheduledOp | tuple, b) -> bool:
    a0, a1 = (a.start, a.end) if isinstance(a, ScheduledOp) else a
    b0, b1 = (b.start, b.end) if isinstance(b, ScheduledOp) else b
    return a0 < b1 and b0 < a1


def validate(sched: Schedule) -> ValidationReport:
    inp, rep = sched.input, ValidationReport()
    cal = _Calendar(inp)
    jobs = {j.key: j for j in inp.jobs}
    bad = rep.violations.append
    placed: dict[tuple[str, str], list[ScheduledOp]] = defaultdict(list)
    for o in sched.ops:
        placed[(o.job_key, o.op_number)].append(o)

    # --- assignments: every required operation exactly once, on an existing, active, qualified machine
    for o in sched.ops:
        rep.checked["assignments"] += 1
        ref = f"{o.job_key} {o.op_number}"
        job = jobs.get(o.job_key)
        if job is None or not any(x.number == o.op_number for x in job.ops):
            bad(Violation("assignments", f"{ref}: not an operation of an included order line", ref))
            continue
        op = job.op(o.op_number)
        machine = inp.machines.get(o.machine)
        if machine is None:
            bad(Violation("assignments", f"{ref}: unknown machine {o.machine}", ref))
        elif not machine.active:
            bad(Violation("assignments", f"{ref}: machine {o.machine} is inactive", ref))
        elif machine.work_center != op.work_center:
            bad(Violation("assignments", f"{ref}: {o.machine} is not in work center {op.work_center}", ref))
        elif not op.capabilities <= machine.capabilities:
            missing = ", ".join(sorted(op.capabilities - machine.capabilities))
            bad(Violation("assignments", f"{ref}: {o.machine} lacks capability {missing}", ref))
        if o.end <= o.start:
            bad(Violation("assignments", f"{ref}: finish is not after start", ref))
        if o.op_number in job.completed:
            bad(Violation("assignments", f"{ref}: already completed, must not be rescheduled", ref))
    for job in inp.jobs:
        blocked = job.blocked_ops()
        for op in job.ops:
            n = len(placed[(job.key, op.number)])
            ref = f"{job.key} {op.number}"
            if op.number in job.completed or op.number in blocked:
                if op.number in blocked and n:
                    bad(Violation("material", f"{ref}: scheduled although blocked ({blocked[op.number]})", ref))
                continue
            if n == 0:
                bad(Violation("assignments", f"{ref}: required operation is missing", ref))
            elif n > 1:
                bad(Violation("assignments", f"{ref}: scheduled {n} times", ref))
            if op.quality_gate:
                rep.checked["inspections"] += 1
                if n == 0:
                    bad(Violation("inspections", f"{ref}: mandatory inspection/test {op.name or op.number} missing", ref))

    def first(key, number) -> ScheduledOp | None:
        lst = placed.get((key, number))
        return lst[0] if lst else None

    # --- precedence and material
    for o in sched.ops:
        job = jobs.get(o.job_key)
        if job is None or not any(x.number == o.op_number for x in job.ops):
            continue
        ref = f"{o.job_key} {o.op_number}"
        for p in job.op(o.op_number).predecessors:
            rep.checked["precedence"] += 1
            if p in job.completed:
                if o.start < job.completed[p]:
                    bad(Violation("precedence", f"{ref} starts before completed {p} finished", ref))
                continue
            pred = first(o.job_key, p)
            if pred is None:
                bad(Violation("precedence", f"{ref}: predecessor {p} is neither completed nor scheduled", ref))
            elif o.start < pred.end:
                bad(Violation("precedence", f"{ref} starts {o.start:%b %d %H:%M} before {p} finishes "
                                            f"{pred.end:%b %d %H:%M}", ref))
        if o.op_number in job.material_ready and not o.fixed:
            rep.checked["material"] += 1
            ready = job.material_ready[o.op_number]
            if ready is None:
                pass  # reported above as a blocked operation
            elif o.start < ready:
                bad(Violation("material", f"{ref} starts before its material is ready ({ready:%b %d})", ref))

    # --- machine overlap and downtime
    by_machine = defaultdict(list)
    for o in sched.ops:
        by_machine[o.machine].append(o)
    for code, ops in by_machine.items():
        ops.sort(key=lambda x: x.start)
        for i, a in enumerate(ops):
            rep.checked["machine_overlap"] += 1
            for b in ops[i + 1:]:
                if b.start >= a.end:
                    break
                bad(Violation("machine_overlap", f"{code}: {a.job_key} {a.op_number} overlaps "
                                                 f"{b.job_key} {b.op_number}", code))
            for d in inp.downtime:
                if d.machine != code or d.status == "Cancelled":
                    continue
                rep.checked["downtime"] += 1
                if _overlap(a, (d.start, d.end)):
                    v = Violation("downtime", f"{a.job_key} {a.op_number} on {code} overlaps "
                                              f"{d.status.lower()} downtime '{d.reason}'", code)
                    (bad if d.status == "Confirmed" else rep.warnings.append)(v)

    # --- calendar: start in working time and enough working time between start and finish
    for o in sched.ops:
        job, machine = jobs.get(o.job_key), inp.machines.get(o.machine)
        if job is None or machine is None or not any(x.number == o.op_number for x in job.ops):
            continue
        rep.checked["calendar"] += 1
        ref = f"{o.job_key} {o.op_number}"
        if not o.fixed and o.start < inp.as_of:
            bad(Violation("calendar", f"{ref} starts before the planning as-of time", ref))
        if not cal.is_working(machine, o.start + STEP / 2):
            bad(Violation("calendar", f"{ref} starts outside working time ({o.start.astimezone(inp.tz):%a %b %d %H:%M})", ref))
        need = required_minutes(job, o.op_number, machine)
        have = cal.working_minutes(machine, o.start, o.end)
        if have + TOLERANCE_MIN < need:
            bad(Violation("calendar", f"{ref}: needs {need:.0f} working min but only {have:.0f} are available "
                                      f"between start and finish", ref))

    # --- work-center capacity per plant day (aggregate check, independent of machine assignment)
    load = defaultdict(float)
    for o in sched.ops:
        machine = inp.machines.get(o.machine)
        if machine is None:
            continue
        t = o.start + STEP / 2
        while t < o.end:
            if cal.is_working(machine, t):
                load[(machine.work_center, t.astimezone(inp.tz).date())] += STEP.total_seconds() / 60
            t += STEP
    for (wc, day), minutes in load.items():
        rep.checked["work_center_capacity"] += 1
        start = datetime.combine(day, datetime.min.time(), inp.tz)
        capacity = sum(cal.working_minutes(m, start, start + timedelta(days=1))
                       for m in inp.machines.values() if m.work_center == wc and m.active)
        if minutes > capacity + TOLERANCE_MIN:
            bad(Violation("work_center_capacity", f"{wc} on {day:%b %d}: {minutes / 60:.1f} h scheduled, "
                                                  f"{capacity / 60:.1f} h available", wc))

    # --- frozen commitments preserved
    for job in inp.jobs:
        for n, f in job.fixed.items():
            rep.checked["frozen"] += 1
            o = first(job.key, n)
            if o is None or (o.machine, o.start, o.end) != (f.machine, f.start, f.end):
                bad(Violation("frozen", f"{job.key} {n}: committed operation ({f.reason}) was moved or dropped",
                              f"{job.key} {n}"))

    # --- delivery-date calculations and completeness (spec §8.3 rule 8)
    for line in sched.lines:
        job = jobs.get(line.key)
        if job is None:
            continue
        rep.checked["delivery"] += 1
        ends = [o.end for o in sched.ops if o.job_key == line.key] + list(job.completed.values())
        if line.status == "blocked":
            if line.completion is not None:
                bad(Violation("delivery", f"{line.key}: blocked line must not report a completion date", line.key))
            continue
        rep.checked["completeness"] += 1
        missing = [op.number for op in job.ops if op.number not in job.completed and not placed.get((job.key, op.number))]
        if missing:
            bad(Violation("completeness", f"{line.key}: reported {line.status} but {', '.join(missing)} not scheduled",
                          line.key))
            continue
        completion = max(ends) if ends else None
        if completion != line.completion:
            bad(Violation("delivery", f"{line.key}: reported completion {line.completion} ≠ last operation finish "
                                      f"{completion}", line.key))
            continue
        lateness = max(0.0, (completion - job.due).total_seconds() / 3600)
        if abs(lateness - line.lateness_hours) > 0.02 or (lateness > 0) != (line.status == "late"):
            bad(Violation("delivery", f"{line.key}: reported lateness {line.lateness_hours} h / {line.status}, "
                                      f"recomputed {lateness:.2f} h", line.key))
    return rep
