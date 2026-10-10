"""Small hand-built scheduling inputs for the M09 tests (fictional). Monday 2025-11-17 06:00 plant time is as-of."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from planning.scheduling.inputs import local_midnight
from planning.scheduling.model import (PLANT_TZ, CalendarException, Downtime, FixedOp, Job, Machine, Options,
                                       RoutingOp, SchedulingInput, Shift)

MONDAY = date(2025, 11, 17)
TWO_SHIFTS = [Shift(time(6), time(14), name="S1"), Shift(time(14), time(22), name="S2")]


def at(day_offset: int, hour: float = 0) -> datetime:
    """Plant-local wall-clock time `day_offset` days after Monday 2025-11-17, as UTC."""
    d = MONDAY + timedelta(days=day_offset)
    local = datetime.combine(d, time(0), PLANT_TZ) + timedelta(hours=hour)
    return local.astimezone(timezone.utc)


def machine(code: str, wc: str, *caps: str, factor: float = 1.0, active: bool = True) -> Machine:
    return Machine(code=code, work_center=wc, capabilities=frozenset(caps), factor=factor, active=active)


def op(number: str, wc: str, hours: float, *caps: str, preds: tuple[str, ...] = (), gate: bool = False,
       setup_h: float = 0.0) -> RoutingOp:
    """A per-unit operation; with the default quantity 1 its work content is `setup_h + hours`."""
    return RoutingOp(number=number, name=number, work_center=wc, setup_min=setup_h * 60, cycle_min=hours * 60,
                     capabilities=frozenset(caps), predecessors=preds, quality_gate=gate, routing_op_id=f"rop-{number}")


def job(order: str, ops: list[RoutingOp], due_day: int, priority: str = "Normal", qty: float = 1, line: int = 1,
        **kw) -> Job:
    """Due at the end of day `due_day` (0 = Monday)."""
    return Job(order_number=order, line_number=line, item="V-TEST", quantity=qty, priority=priority,
               due=local_midnight(MONDAY + timedelta(days=due_day + 1)), ops=ops,
               production_order=f"PRD-{order.removeprefix('SO-')}-{line}", **kw)


def make_input(machines: list[Machine], jobs: list[Job], downtime: list[Downtime] = (),
               exceptions: list[CalendarException] = (), horizon_days: int = 14, **opts) -> SchedulingInput:
    wcs = {m.work_center for m in machines}
    options = Options(time_limit_s=opts.pop("time_limit_s", 5.0), **opts)
    return SchedulingInput(as_of=at(0, 0), horizon_end=at(horizon_days), machines={m.code: m for m in machines},
                           wc_shifts={wc: list(TWO_SHIFTS) for wc in wcs}, machine_shifts={},
                           exceptions=list(exceptions), downtime=list(downtime), jobs=list(jobs), options=options)


__all__ = ["at", "machine", "op", "job", "make_input", "Downtime", "FixedOp", "CalendarException", "Shift", "MONDAY"]
