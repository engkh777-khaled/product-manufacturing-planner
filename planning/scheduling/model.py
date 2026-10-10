"""Finite-capacity scheduling data structures (spec §8).

All datetimes are timezone-aware UTC. Shift times are plant-local wall-clock times, converted per day, so daylight
saving changes are handled by the calendar, not by the solver. One `Job` is one production order for one sales
order line.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

PLANT_TZ = ZoneInfo("America/Chicago")
PRIORITY_WEIGHT = {"Critical": 8, "High": 4, "Normal": 2, "Low": 1}


@dataclass(frozen=True)
class Machine:
    code: str
    work_center: str
    capabilities: frozenset[str] = frozenset()
    factor: float = 1.0          # relative speed: duration on this machine = standard duration / factor
    active: bool = True
    name: str = ""


@dataclass(frozen=True)
class Shift:
    """A recurring working window in plant-local time. end <= start means the shift crosses midnight."""
    start: time
    end: time
    days: frozenset[int] = frozenset({1, 2, 3, 4, 5})   # ISO weekdays
    valid_from: date | None = None
    valid_to: date | None = None
    name: str = ""


@dataclass(frozen=True)
class CalendarException:
    """Holiday / shutdown removes working time; approved overtime adds it. No resource = plant-wide."""
    kind: str                 # holiday | shutdown | overtime
    start: datetime
    end: datetime
    work_center: str | None = None
    machine: str | None = None
    approved: bool = True
    description: str = ""

    def applies_to(self, machine: Machine) -> bool:
        return (self.machine is None or self.machine == machine.code) and \
               (self.work_center is None or self.work_center == machine.work_center)


@dataclass(frozen=True)
class Downtime:
    machine: str
    start: datetime
    end: datetime
    status: str = "Confirmed"  # Planned | Confirmed | Cancelled — only Confirmed blocks (spec §6.15)
    reason: str = ""


@dataclass(frozen=True)
class RoutingOp:
    number: str
    name: str
    work_center: str
    setup_min: float = 0.0
    cycle_min: float = 0.0
    inspection_min: float = 0.0
    basis: str = "per_unit"            # per_unit | per_batch
    max_batch: int | None = None
    capabilities: frozenset[str] = frozenset()
    predecessors: tuple[str, ...] = ()
    quality_gate: bool = False
    routing_op_id: str | None = None

    def work_minutes(self, quantity: float) -> float:
        """Spec §8.2, charging rule (D-027): setup and inspection once per production order; cycle per unit, or per
        batch of at most `max_batch` units for batch operations (ovens, coating lines)."""
        if self.basis == "per_batch":
            runs = math.ceil(quantity / self.max_batch - 1e-9) if self.max_batch else 1
        else:
            runs = quantity
        return self.setup_min + runs * self.cycle_min + self.inspection_min


@dataclass(frozen=True)
class FixedOp:
    """A committed operation that the scheduler must not move (frozen, released or in progress)."""
    machine: str
    start: datetime
    end: datetime
    reason: str = "frozen"


@dataclass
class Job:
    order_number: str
    line_number: int
    item: str
    quantity: float
    priority: str
    due: datetime                                   # end of the requested delivery date (plant time)
    ops: list[RoutingOp]
    production_order: str = ""
    status: str = "Confirmed"
    material_ready: dict[str, datetime | None] = field(default_factory=dict)  # op -> earliest start; None = short
    material_notes: dict[str, str] = field(default_factory=dict)
    completed: dict[str, datetime] = field(default_factory=dict)              # op -> actual finish
    fixed: dict[str, FixedOp] = field(default_factory=dict)
    baseline: dict[str, tuple[str, datetime, datetime]] = field(default_factory=dict)  # op -> (machine, start, end)
    baseline_finish: datetime | None = None

    @property
    def key(self) -> str:
        return f"{self.order_number}/{self.line_number}"

    @property
    def weight(self) -> int:
        return PRIORITY_WEIGHT.get(self.priority, 1)

    def op(self, number: str) -> RoutingOp:
        return next(o for o in self.ops if o.number == number)

    def successors(self, number: str) -> list[str]:
        return [o.number for o in self.ops if number in o.predecessors]

    def blocked_ops(self) -> dict[str, str]:
        """Operations that cannot run in this plan: material short (spec acceptance test 2), plus everything after."""
        blocked: dict[str, str] = {}
        for o in self.ops:
            if o.number in self.material_ready and self.material_ready[o.number] is None and o.number not in self.completed:
                blocked[o.number] = self.material_notes.get(o.number, "material short")
        frontier = list(blocked)
        while frontier:
            current = frontier.pop()
            for nxt in self.successors(current):
                if nxt not in blocked and nxt not in self.completed:
                    blocked[nxt] = f"after {current} (blocked)"
                    frontier.append(nxt)
        return blocked


@dataclass(frozen=True)
class DataIssue:
    order_number: str
    line_number: int
    kind: str        # missing_bom | missing_routing | no_qualified_machine | no_calendar | no_window
    detail: str


@dataclass
class Weights:
    """Configurable objective (spec §8.4). Units: per 30-minute slot unless noted."""
    lateness: int = 10           # × priority weight, per slot late
    late_order: int = 500        # × priority weight, once per late line
    baseline_change: int = 1     # per slot an operation moves from its baseline start
    flow: int = 1                # per slot of operation end time (keeps the schedule compact, finishes early)


@dataclass
class Options:
    time_limit_s: float = 10.0
    workers: int = 8
    seed: int = 7
    slot_minutes: int = 30
    freeze_days: int = 0                 # baseline operations starting within N days of as-of are frozen
    include_drafts: bool = False         # draft orders are not commitments (D-028)
    orders: frozenset[str] | None = None  # only these sales orders (None = all open)
    overflow_days: int = 21              # model time beyond the horizon / latest due date
    window_days: float = 3.0             # search window: until N days after max(due, greedy finish)
    weights: Weights = field(default_factory=Weights)


@dataclass
class SchedulingInput:
    as_of: datetime
    horizon_end: datetime
    machines: dict[str, Machine]
    wc_shifts: dict[str, list[Shift]]
    machine_shifts: dict[str, list[Shift]]
    exceptions: list[CalendarException]
    downtime: list[Downtime]
    jobs: list[Job]
    issues: list[DataIssue] = field(default_factory=list)
    options: Options = field(default_factory=Options)
    tz: ZoneInfo = PLANT_TZ
    snapshot_at: str | None = None
    notes: list[str] = field(default_factory=list)

    def shifts_for(self, machine: Machine) -> list[Shift]:
        return self.machine_shifts.get(machine.code) or self.wc_shifts.get(machine.work_center, [])

    def job(self, key: str) -> Job:
        return next(j for j in self.jobs if j.key == key)


@dataclass
class ScheduledOp:
    order_number: str
    line_number: int
    op_number: str
    machine: str
    start: datetime
    end: datetime
    work_minutes: float
    fixed: bool = False

    @property
    def job_key(self) -> str:
        return f"{self.order_number}/{self.line_number}"


@dataclass
class LineResult:
    order_number: str
    line_number: int
    item: str
    priority: str
    due: datetime
    status: str                           # on_time | late | blocked | needs_data
    completion: datetime | None = None
    lateness_hours: float = 0.0
    earliest_possible: datetime | None = None
    achievable: bool | None = None        # False: requested date impossible under the modeled constraints
    blocked_ops: dict[str, str] = field(default_factory=dict)
    limiting: list[str] = field(default_factory=list)
    baseline_finish: datetime | None = None

    @property
    def key(self) -> str:
        return f"{self.order_number}/{self.line_number}"


@dataclass
class Schedule:
    input: SchedulingInput
    outcome: str                          # spec §8.7 vocabulary
    solver_status: str                    # CP-SAT status name
    objective: float | None
    seconds: float
    ops: list[ScheduledOp] = field(default_factory=list)
    lines: list[LineResult] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)   # reasons for Infeasible found before/after solving

    def ops_for(self, key: str) -> list[ScheduledOp]:
        return sorted((o for o in self.ops if o.job_key == key), key=lambda o: o.start)

    def line(self, key: str) -> LineResult:
        return next(l for l in self.lines if l.key == key)
