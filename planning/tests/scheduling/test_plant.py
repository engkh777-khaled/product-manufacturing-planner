"""Golden tests on the recorded plant snapshot (fictional): the whole demo factory, solved once per module."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from planning.scheduling import payload, plan
from planning.scheduling.inputs import local_midnight
from planning.scheduling.model import Options

FIXTURE = json.loads((Path(__file__).parents[3] / "tests" / "fixtures" / "snapshot.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def plant():
    sched, report = plan(FIXTURE, (date(2025, 11, 17), date(2025, 12, 15)), Options(time_limit_s=6))
    return sched, report, payload(sched, report, datetime.now(timezone.utc))


def ops_of(sched, key):
    return {o.op_number: o for o in sched.ops_for(key)}


def test_plant_schedule_is_valid_and_reports_lateness_honestly(plant):
    sched, report, _ = plant
    assert sched.outcome == "Feasible with lateness"               # two lines are blocked by material
    assert report.passed, report.violations
    assert sum(report.checked.values()) > 200
    assert {l.key for l in sched.lines} >= {"SO-1005/1", "SO-1012/1", "SO-1022/1"}


def test_2_missing_component_blocks_the_operation_not_the_whole_plan(plant):
    sched, _, body = plant
    line = sched.line("SO-1005/1")
    assert line.status == "blocked" and line.completion is None
    assert "SR-ST6-10 short 3" in line.blocked_ops["OP-030"]
    assert set(ops_of(sched, "SO-1005/1")) == {"OP-010", "OP-020"}  # machining before the seat rings still runs
    order = next(o for o in body["orders"] if o["production_order_number"] == "PRD-1005-1")
    assert order["planned_finish"] is None
    assert any(r["notes"] and r["notes"].startswith("Not scheduled: SR-ST6-10 short 3") for r in order["operations"])


def test_3_late_supplier_receipt_delays_the_consuming_operation(plant):
    sched, _, _ = plant
    weld = ops_of(sched, "SO-1012/1")["OP-050"]
    assert weld.start >= local_midnight(date(2025, 11, 25))         # studs from PO-8815 arrive Nov 25
    assert sched.line("SO-1012/1").status == "on_time"              # still meets Dec 15


def test_completed_operations_are_not_rescheduled(plant):
    sched, _, body = plant
    assert not {"OP-030", "OP-060"} & set(ops_of(sched, "SO-1014/1"))
    order = next(o for o in body["orders"] if o["production_order_number"] == "PRD-1014-1")
    done = [r for r in order["operations"] if r.get("status") == "Completed"]
    assert {r["operation_number"] for r in done} == {"OP-030", "OP-060"} and all(r["actual_finish"] for r in done)


def test_confirmed_downtime_is_respected(plant):
    sched, _, _ = plant
    pm_start, pm_end = datetime(2025, 11, 21, 20, tzinfo=timezone.utc), datetime(2025, 11, 22, 4, tzinfo=timezone.utc)
    assert not [o for o in sched.ops if o.machine == "MC-010" and o.start < pm_end and pm_start < o.end]


def test_payload_is_ready_for_record_schedule_run(plant):
    _, _, body = plant
    assert body["horizon_start"] == "2025-11-17" and body["horizon_end"] == "2025-12-15"
    assert body["status"] == "Feasible with lateness" and body["version"]["validation_passed"] is True
    rows = [r for o in body["orders"] for r in o["operations"]]
    assert rows and all(r["routing_operation_id"] for r in rows)
    metrics = body["version"]["metrics"]
    assert metrics["late_order_numbers"] == ["SO-1005", "SO-1022"]
    assert metrics["work_center_load"]["WC-10"]["capacity_hours"] > 0
    assert 0 <= metrics["machine_utilization"]["MC-010"] <= 100
    json.dumps(body)                                               # JSON-serialisable for the worker / RPC
