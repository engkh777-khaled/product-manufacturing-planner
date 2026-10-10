"""The independent validator (spec §8.8) must catch every kind of broken schedule: each test corrupts a valid
solver result in one way and expects the matching check to fail."""

from __future__ import annotations

import copy

import pytest

from planning.scheduling import solve
from planning.tests.scheduling.factory import at, job, machine, make_input, op
from planning.validation import validate


@pytest.fixture(scope="module")
def good():
    ops = [op("OP-010", "WC-10", 4, "turning"),
           op("OP-020", "WC-40", 2, "hydro", preds=("OP-010",), gate=True),
           op("OP-030", "WC-30", 1, "final-inspection", preds=("OP-020",), gate=True)]
    inp = make_input([machine("MC-1", "WC-10", "turning"), machine("MC-2", "WC-10", "milling"),
                      machine("TE-1", "WC-40", "hydro"), machine("SA-1", "WC-30", "final-inspection")],
                     [job("SO-1", ops, 2), job("SO-2", [op("OP-010", "WC-10", 3, "turning")], 2)])
    sched = solve(inp)
    assert validate(sched).passed
    return sched


def broken(sched, change) -> set[str]:
    s = copy.deepcopy(sched)
    change(s, {(o.job_key, o.op_number): o for o in s.ops})
    return {v.check for v in validate(s).violations}


def test_valid_schedule_passes_every_check(good):
    report = validate(good)
    assert report.passed and not report.warnings
    assert all(report.checked[c] > 0 for c in ("assignments", "inspections", "precedence", "machine_overlap",
                                               "calendar", "work_center_capacity", "delivery", "completeness"))
    assert report.checked["precedence"] == 2 and report.checked["inspections"] == 2


def test_precedence_violation(good):
    def change(s, ops):
        ops[("SO-1/1", "OP-020")].start = ops[("SO-1/1", "OP-010")].start
    assert "precedence" in broken(good, change)


def test_unqualified_machine(good):
    def change(s, ops):
        ops[("SO-1/1", "OP-010")].machine = "MC-2"          # same work center, lacks 'turning'
    assert "assignments" in broken(good, change)


def test_wrong_work_center_and_unknown_machine(good):
    def change(s, ops):
        ops[("SO-1/1", "OP-010")].machine = "TE-1"
        ops[("SO-2/1", "OP-010")].machine = "XX-404"
    assert "assignments" in broken(good, change)


def test_missing_inspection_and_incomplete_order(good):
    def change(s, ops):
        s.ops.remove(ops[("SO-1/1", "OP-030")])
    assert {"inspections", "assignments", "completeness"} <= broken(good, change)


def test_duplicate_operation(good):
    def change(s, ops):
        s.ops.append(copy.deepcopy(ops[("SO-2/1", "OP-010")]))
    assert {"assignments", "machine_overlap"} <= broken(good, change)


def test_start_outside_shifts(good):
    def change(s, ops):
        o = ops[("SO-2/1", "OP-010")]
        o.start, o.end = at(0, 23), at(1, 9)                # 23:00 start, Tuesday morning finish
    assert "calendar" in broken(good, change)


def test_too_little_working_time(good):
    def change(s, ops):
        o = ops[("SO-1/1", "OP-010")]
        o.end = o.start + (o.end - o.start) / 2
    assert "calendar" in broken(good, change)


def test_work_center_overload(good):
    def change(s, ops):
        # three 16-hour jobs stacked onto one turning machine on the same day
        o = ops[("SO-2/1", "OP-010")]
        o.start, o.end = at(0, 6), at(0, 22)
        o.work_minutes = 960
        for k in range(2):
            extra = copy.deepcopy(o)
            s.ops.append(extra)
    checks = broken(good, change)
    assert {"machine_overlap", "work_center_capacity"} <= checks


def test_wrong_completion_date(good):
    def change(s, ops):
        s.line("SO-1/1").completion = at(0, 10)
    assert "delivery" in broken(good, change)


def test_wrong_lateness_flag(good):
    def change(s, ops):
        line = s.line("SO-2/1")
        line.status, line.lateness_hours = "late", 5.0
    assert "delivery" in broken(good, change)


def test_before_as_of(good):
    def change(s, ops):
        o = ops[("SO-2/1", "OP-010")]
        o.start, o.end = at(-3, 6), at(-3, 9)              # Friday before as-of
    assert "calendar" in broken(good, change)
