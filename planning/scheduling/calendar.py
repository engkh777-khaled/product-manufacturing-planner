"""Working calendars for the solver: shifts − holidays/shutdowns + approved overtime, per machine, as UTC windows;
then a slot grid (spec §6.14, §8.3 rule 5). Confirmed downtime is not working time *and* may not be overlapped
by any operation (handled when building start tables)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from planning.scheduling.model import Machine, SchedulingInput

Window = tuple[datetime, datetime]


def merge(windows: list[Window]) -> list[Window]:
    out: list[Window] = []
    for a, b in sorted(w for w in windows if w[1] > w[0]):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def subtract(windows: list[Window], cuts: list[Window]) -> list[Window]:
    result = []
    cuts = merge(cuts)
    for a, b in windows:
        pieces = [(a, b)]
        for c, d in cuts:
            nxt = []
            for x, y in pieces:
                if d <= x or c >= y:
                    nxt.append((x, y))
                    continue
                if c > x:
                    nxt.append((x, c))
                if d < y:
                    nxt.append((d, y))
            pieces = nxt
        result.extend(pieces)
    return merge(result)


def working_windows(inp: SchedulingInput, machine: Machine, start: datetime, end: datetime) -> list[Window]:
    tz = inp.tz
    first = start.astimezone(tz).date() - timedelta(days=1)
    last = end.astimezone(tz).date() + timedelta(days=1)
    windows: list[Window] = []
    for shift in inp.shifts_for(machine):
        d = first
        while d <= last:
            valid = (shift.valid_from is None or d >= shift.valid_from) and (shift.valid_to is None or d <= shift.valid_to)
            if valid and d.isoweekday() in shift.days:
                a = datetime.combine(d, shift.start, tz)
                b = datetime.combine(d + timedelta(days=1) if shift.end <= shift.start else d, shift.end, tz)
                windows.append((a.astimezone(timezone.utc), b.astimezone(timezone.utc)))
            d += timedelta(days=1)
    windows = merge(windows)
    off = [(e.start, e.end) for e in inp.exceptions if e.kind in ("holiday", "shutdown") and e.applies_to(machine)]
    windows = subtract(windows, off)
    overtime = [(e.start, e.end) for e in inp.exceptions if e.kind == "overtime" and e.approved and e.applies_to(machine)]
    windows = merge(windows + overtime)
    return [(max(a, start), min(b, end)) for a, b in windows if b > start and a < end]


@dataclass
class SlotGrid:
    origin: datetime
    slot_minutes: int
    size: int

    def at(self, slot: int) -> datetime:
        return self.origin + timedelta(minutes=slot * self.slot_minutes)

    def floor(self, t: datetime) -> int:
        return math.floor((t - self.origin).total_seconds() / 60 / self.slot_minutes)

    def ceil(self, t: datetime) -> int:
        return math.ceil((t - self.origin).total_seconds() / 60 / self.slot_minutes - 1e-9)

    def slots(self, minutes: float) -> int:
        return max(1, math.ceil(minutes / self.slot_minutes - 1e-9))


def working_slots(grid: SlotGrid, windows: list[Window]) -> list[bool]:
    """A slot counts as working only if it lies completely inside working time."""
    flags = [False] * grid.size
    for a, b in windows:
        for s in range(max(0, grid.ceil(a)), min(grid.size, grid.floor(b))):
            flags[s] = True
    return flags


def blocked_slots(grid: SlotGrid, intervals: list[Window]) -> list[bool]:
    """Slots touched by an interval (downtime, committed work): conservative, any overlap blocks the slot."""
    flags = [False] * grid.size
    for a, b in intervals:
        for s in range(max(0, grid.floor(a)), min(grid.size, grid.ceil(b))):
            flags[s] = True
    return flags


def start_table(working: list[bool], blocked: list[bool], duration: int) -> tuple[list[int], list[int]]:
    """For every slot s: ok[s] = 1 if an operation needing `duration` working slots can start at s on this machine
    without touching a blocked slot before it finishes; end[s] = slot after its last working slot.
    Work pauses outside working time (nights, weekends, holidays) and resumes in the next shift."""
    n = len(working)
    positions = [i for i, w in enumerate(working) if w]
    rank = {p: k for k, p in enumerate(positions)}
    # next blocked slot at or after i
    next_block = [n] * (n + 1)
    for i in range(n - 1, -1, -1):
        next_block[i] = i if blocked[i] else next_block[i + 1]
    ok, end = [0] * n, [0] * n
    for s in range(n):
        if not working[s] or blocked[s]:
            end[s] = min(n, s + duration)
            continue
        k = rank[s] + duration - 1
        if k >= len(positions):
            end[s] = n
            continue
        finish = positions[k] + 1
        if next_block[s] >= finish:
            ok[s], end[s] = 1, finish
        else:
            end[s] = finish
    return ok, end


def start_pieces(ok: list[int], end: list[int], release: int = 0) -> list[tuple[int, int, int]]:
    """Compress a start table into pieces (lo, hi, elapsed): for every start s in [lo, hi] the operation finishes at
    s + elapsed. Within one working window the finish slides with the start, so there are only a few pieces per day
    — a much smaller CP-SAT model than an element constraint over every slot."""
    pieces, cur = [], None
    for s in range(max(0, release), len(ok)):
        if ok[s]:
            elapsed = end[s] - s
            if cur and cur[1] == s - 1 and cur[2] == elapsed:
                cur[1] = s
                continue
            if cur:
                pieces.append(tuple(cur))
            cur = [s, s, elapsed]
        elif cur:
            pieces.append(tuple(cur))
            cur = None
    if cur:
        pieces.append(tuple(cur))
    return pieces
