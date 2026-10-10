"""M09 test gate: spec §14 acceptance tests 1, 4, 5, 6, 7, 8 for the finite-capacity scheduler, on small fictional
plants built by hand (two shifts, 06:00–22:00 Monday–Friday, plant time). Test 8 also runs on the recorded
snapshot; tests 2/3 on the real snapshot are in test_plant.py."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

from planning.scheduling import from_snapshot, payload, solve
from planning.scheduling.model import FixedOp, Options
from planning.tests.scheduling.factory import Downtime, at, job, machine, make_input, op
from planning.validation import validate

FIXTURE = json.loads((Path(__file__).parents[3] / "tests" / "fixtures" / "snapshot.json").read_text(encoding="utf-8"))


def by_op(sched):
    return {(o.job_key, o.op_number): o for o in sched.ops}


# ------------------------------------------------------------------ Test 1: normal order

def test_1_normal_order_gets_a_feasible_validated_schedule():
    ops = [op("OP-010", "WC-10", 4, "turning"),
           op("OP-020", "WC-40", 2, "hydro", preds=("OP-010",), gate=True),
           op("OP-030", "WC-30", 1, "final-inspection", preds=("OP-020",), gate=True)]
    inp = make_input([machine("MC-1", "WC-10", "turning"), machine("TE-1", "WC-40", "hydro"),
                      machine("SA-1", "WC-30", "final-inspection")],
                     [job("SO-1", ops, due_day=4, material_ready={"OP-010": at(0)})])
    sched = solve(inp)
    report = validate(sched)
    assert sched.outcome == "Feasible" and sched.solver_status in ("OPTIMAL", "FEASIBLE")
    assert report.passed, report.violations
    placed = by_op(sched)
    assert placed[("SO-1/1", "OP-010")].start == at(0, 6)          # Monday 06:00, first shift
    assert placed[("SO-1/1", "OP-020")].start >= placed[("SO-1/1", "OP-010")].end
    assert placed[("SO-1/1", "OP-030")].end == at(0, 13)           # 4 + 2 + 1 h → 13:00
    line = sched.line("SO-1/1")
    assert line.status == "on_time" and line.lateness_hours == 0 and line.achievable
    # the plan can be saved (and then submitted): a validated Draft version payload with every operation
    body = payload(sched, report, datetime.now(timezone.utc))
    assert body["status"] == "Feasible" and body["version"]["validation_passed"] is True
    assert [r["operation_number"] for r in body["orders"][0]["operations"]] == ["OP-010", "OP-020", "OP-030"]
    assert report.checked["inspections"] == 2


def test_work_pauses_outside_shifts_and_resumes_next_day():
    inp = make_input([machine("MC-1", "WC-10", "turning")], [job("SO-1", [op("OP-010", "WC-10", 20, "turning")], 4)])
    sched = solve(inp)
    o = sched.ops[0]
    assert (o.start, o.end) == (at(0, 6), at(1, 10))               # 16 h Monday + 4 h Tuesday
    assert validate(sched).passed


def test_holiday_and_weekend_are_not_working_time_but_approved_overtime_is():
    from planning.scheduling.model import CalendarException
    holiday = CalendarException("holiday", at(1, 0), at(2, 0), description="Plant holiday")
    overtime = CalendarException("overtime", at(5, 6), at(5, 14), work_center="WC-10", description="Saturday OT")
    unapproved = CalendarException("overtime", at(0, 22), at(1, 6), work_center="WC-10", approved=False)
    inp = make_input([machine("MC-1", "WC-10", "turning")],
                     [job("SO-1", [op("OP-010", "WC-10", 56, "turning")], 10)],
                     exceptions=[holiday, overtime, unapproved])
    sched = solve(inp)
    o = sched.ops[0]
    # Mon 16 h, Tue holiday, Wed 16 h, Thu 16 h, Fri 8 h → ends Friday 14:00
    assert (o.start, o.end) == (at(0, 6), at(4, 14))
    assert validate(sched).passed
    # with 72 h the Saturday overtime shift is used: Fri 16 h, Sat 06–14 → ends Saturday 14:00
    inp2 = make_input([machine("MC-1", "WC-10", "turning")], [job("SO-1", [op("OP-010", "WC-10", 72, "turning")], 10)],
                      exceptions=[holiday, overtime])
    o2 = solve(inp2).ops[0]
    assert o2.end == at(5, 14)


# ------------------------------------------------------------------ Test 4: machine conflict

def test_4_operations_on_the_same_exclusive_machine_do_not_overlap():
    jobs = [job("SO-1", [op("OP-010", "WC-10", 6, "turning")], 5), job("SO-2", [op("OP-010", "WC-10", 6, "turning")], 5)]
    sched = solve(make_input([machine("MC-1", "WC-10", "turning"), machine("MC-9", "WC-10", "milling")], jobs))
    a, b = sorted(sched.ops, key=lambda o: o.start)
    assert a.machine == b.machine == "MC-1"                        # MC-9 is not qualified (no turning)
    assert b.start >= a.end
    report = validate(sched)
    assert report.passed

    # the validator detects an invalid overlap
    bad = copy.deepcopy(sched)
    bad.ops[1].start, bad.ops[1].end = bad.ops[0].start, bad.ops[0].end
    assert "machine_overlap" in {v.check for v in validate(bad).violations}


def test_4_a_qualified_alternative_machine_is_used():
    jobs = [job("SO-1", [op("OP-010", "WC-10", 6, "turning")], 0), job("SO-2", [op("OP-010", "WC-10", 6, "turning")], 0)]
    sched = solve(make_input([machine("MC-1", "WC-10", "turning"), machine("MC-2", "WC-10", "turning", "milling")], jobs))
    assert {o.machine for o in sched.ops} == {"MC-1", "MC-2"}
    assert all(o.start == at(0, 6) for o in sched.ops)              # parallel, both on time
    assert sched.outcome == "Feasible" and validate(sched).passed


def test_slower_machine_takes_longer():
    sched = solve(make_input([machine("MC-1", "WC-10", "turning", factor=0.5)],
                             [job("SO-1", [op("OP-010", "WC-10", 4, "turning")], 3)]))
    o = sched.ops[0]
    assert o.end - o.start == at(0, 14) - at(0, 6)                  # 4 h of standard work at half speed = 8 h
    assert validate(sched).passed


# ------------------------------------------------------------------ Test 5: urgent order

def _competing(freeze: bool):
    existing = job("SO-1", [op("OP-010", "WC-10", 12, "turning")], 0, priority="Normal",
                   baseline={"OP-010": ("MC-1", at(0, 6), at(0, 18))}, baseline_finish=at(0, 18))
    if freeze:
        existing.fixed["OP-010"] = FixedOp("MC-1", at(0, 6), at(0, 18), "frozen")
    urgent = job("SO-9", [op("OP-010", "WC-10", 12, "turning")], 0, priority="Critical")
    return make_input([machine("MC-1", "WC-10", "turning")], [existing, urgent])


def test_5_urgent_order_takes_priority_and_the_delivery_change_is_reported():
    sched = solve(_competing(freeze=False))
    urgent, existing = sched.line("SO-9/1"), sched.line("SO-1/1")
    assert urgent.status == "on_time" and urgent.completion == at(0, 18)
    assert existing.status == "late" and existing.completion == at(1, 14)   # pushed behind the urgent order
    body = payload(sched, validate(sched), datetime.now(timezone.utc))
    changes = {l["order_number"]: l["change_vs_baseline_days"] for l in body["version"]["metrics"]["lines"]}
    assert changes["SO-1"] > 0                                     # delivery-date change vs the approved baseline
    assert any("SO-9/1" in f for f in existing.limiting)           # explained: capacity taken by the urgent order
    assert validate(sched).passed


def test_5_frozen_commitments_are_preserved_under_the_freeze_policy():
    sched = solve(_competing(freeze=True))
    placed = by_op(sched)
    assert (placed[("SO-1/1", "OP-010")].start, placed[("SO-1/1", "OP-010")].end) == (at(0, 6), at(0, 18))
    assert placed[("SO-1/1", "OP-010")].fixed
    urgent = sched.line("SO-9/1")
    assert urgent.status == "late" and urgent.achievable is False
    assert any("Committed work on MC-1" in f for f in urgent.limiting)
    report = validate(sched)
    assert report.passed and report.checked["frozen"] == 1
    moved = copy.deepcopy(sched)
    next(o for o in moved.ops if o.fixed).start = at(0, 7)
    assert "frozen" in {v.check for v in validate(moved).violations}


def test_freeze_window_option_fixes_baseline_operations_from_the_snapshot():
    inp = from_snapshot(FIXTURE, options=Options(freeze_days=3))
    frozen = {(j.key, n) for j in inp.jobs for n in j.fixed}
    assert ("SO-1012/1", "OP-015") in frozen                       # baseline start Nov 17 (inside 3 days)
    assert ("SO-1012/1", "OP-060") not in frozen                   # Nov 27: outside the window


# ------------------------------------------------------------------ Test 6: machine downtime

def test_6_operation_is_not_scheduled_during_confirmed_downtime():
    down = Downtime("MC-1", at(0, 6), at(0, 14), "Confirmed", "Spindle PM")
    planned = Downtime("MC-1", at(1, 14), at(1, 16), "Planned", "Possible inspection")
    sched = solve(make_input([machine("MC-1", "WC-10", "turning")],
                             [job("SO-1", [op("OP-010", "WC-10", 8, "turning")], 3)], downtime=[down, planned]))
    o = sched.ops[0]
    assert o.start >= at(0, 14) and (o.start, o.end) == (at(0, 14), at(0, 22))
    report = validate(sched)
    assert report.passed
    bad = copy.deepcopy(sched)
    bad.ops[0].start, bad.ops[0].end = at(0, 6), at(0, 14)
    assert "downtime" in {v.check for v in validate(bad).violations}


def test_6_long_operation_never_straddles_downtime_and_moves_after_it():
    down = Downtime("MC-1", at(0, 20), at(0, 22), "Confirmed", "Breakdown")
    sched = solve(make_input([machine("MC-1", "WC-10", "turning")],
                             [job("SO-1", [op("OP-010", "WC-10", 10, "turning")], 3)], downtime=[down]))
    o = sched.ops[0]
    # 06:00 + 10 h would end at 16:00 — before the breakdown, so it may start at 06:00
    assert (o.start, o.end) == (at(0, 6), at(0, 16))
    sched2 = solve(make_input([machine("MC-1", "WC-10", "turning")],
                              [job("SO-1", [op("OP-010", "WC-10", 16, "turning")], 3)], downtime=[down]))
    o2 = sched2.ops[0]
    assert o2.start >= at(0, 22) and validate(sched2).passed      # cannot fit 16 h before the breakdown


def test_6_committed_work_inside_downtime_is_reported_infeasible():
    existing = job("SO-1", [op("OP-010", "WC-10", 4, "turning")], 3,
                   fixed={"OP-010": FixedOp("MC-1", at(0, 8), at(0, 12), "in progress")})
    sched = solve(make_input([machine("MC-1", "WC-10", "turning")], [existing],
                             downtime=[Downtime("MC-1", at(0, 6), at(0, 14), "Confirmed", "Breakdown")]))
    assert sched.outcome == "Infeasible" and not sched.ops
    assert "overlaps confirmed downtime 'Breakdown'" in sched.conflicts[0]


# ------------------------------------------------------------------ Test 7: impossible requested date

def test_7_impossible_requested_date_is_reported_with_limiting_factors():
    ops = [op("OP-010", "WC-10", 20, "turning"), op("OP-020", "WC-40", 10, "hydro", preds=("OP-010",), gate=True)]
    sched = solve(make_input([machine("MC-1", "WC-10", "turning"), machine("TE-1", "WC-40", "hydro")],
                             [job("SO-1", ops, due_day=0, priority="Critical")]))
    line = sched.line("SO-1/1")
    assert line.status == "late" and line.achievable is False
    assert line.earliest_possible == line.completion == at(1, 20)  # 30 working h from Monday 06:00
    assert "cannot be achieved under the modeled constraints" in line.limiting[0]
    assert any("Processing time: 30.0 working h along OP-010→OP-020" in f for f in line.limiting)
    assert sched.outcome == "Feasible with lateness"               # valid schedule, missed date — not fabricated
    assert validate(sched).passed


def test_7_material_is_named_when_it_limits_the_date():
    ops = [op("OP-010", "WC-10", 2, "turning")]
    sched = solve(make_input([machine("MC-1", "WC-10", "turning")],
                             [job("SO-1", ops, due_day=1, material_ready={"OP-010": at(3)},
                                  material_notes={"OP-010": "ST-IN718-M24 from PO-8815/1 on Nov 20"})]))
    line = sched.line("SO-1/1")
    o = sched.ops[0]
    assert o.start == at(3, 6)                                     # test 3: cannot start before material readiness
    assert line.achievable is False and any("Material for OP-010: ST-IN718-M24" in f for f in line.limiting)
    bad = copy.deepcopy(sched)
    bad.ops[0].start, bad.ops[0].end = at(0, 6), at(0, 8)
    assert "material" in {v.check for v in validate(bad).violations}


# ------------------------------------------------------------------ Test 2 (synthetic): short material blocks the operation

def test_short_material_blocks_the_operation_and_its_successors():
    ops = [op("OP-010", "WC-10", 2, "turning"), op("OP-020", "WC-10", 2, "turning", preds=("OP-010",)),
           op("OP-030", "WC-10", 2, "turning", preds=("OP-020",))]
    sched = solve(make_input([machine("MC-1", "WC-10", "turning")],
                             [job("SO-1", ops, 3, material_ready={"OP-020": None},
                                  material_notes={"OP-020": "SR-ST6-10 short 3"})]))
    line = sched.line("SO-1/1")
    assert [o.op_number for o in sched.ops] == ["OP-010"]          # work before the shortage still runs
    assert line.status == "blocked" and line.completion is None
    assert line.blocked_ops == {"OP-020": "SR-ST6-10 short 3", "OP-030": "after OP-020 (blocked)"}
    assert sched.outcome == "Feasible with lateness" and validate(sched).passed


# ------------------------------------------------------------------ Test 8: missing engineering data

def test_8_order_without_approved_engineering_data_needs_data():
    sched = solve(from_snapshot(FIXTURE, options=Options(include_drafts=True, time_limit_s=3,
                                                         orders=frozenset({"SO-1021", "SO-1015"}))))
    assert sched.outcome == "Needs data"
    line = sched.line("SO-1021/1")
    assert line.status == "needs_data" and "V-800-FGV-02 has no approved BOM" in line.limiting[0]
    assert not [o for o in sched.ops if o.order_number == "SO-1021"]   # not treated as production-ready
    assert sched.line("SO-1015/1").status == "on_time"                # the rest is still planned
    assert validate(sched).passed


def test_8_no_qualified_machine_is_missing_data():
    snap = copy.deepcopy(FIXTURE)
    next(m for m in snap["machines"] if m["machine_code"] == "VT-001")["is_active"] = False
    inp = from_snapshot(snap, options=Options(orders=frozenset({"SO-1012"})))
    assert [(i.order_number, i.kind) for i in inp.issues] == [("SO-1012", "no_qualified_machine")]
    assert "OP-015" in inp.issues[0].detail and not inp.jobs


def test_drafts_are_not_scheduled_by_default():
    keys = {j.key for j in from_snapshot(FIXTURE).jobs}
    assert "SO-1021/1" not in keys and "SO-1005/1" in keys
