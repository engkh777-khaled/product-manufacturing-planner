"""M12 solver performance: a synthetic plant several times the demo size (fictional) must always get a validated
schedule within the time budget — CP-SAT's best solution, or the proven-feasible greedy schedule at the limit."""

from __future__ import annotations

import random
import time

import pytest

from planning.scheduling import solve
from planning.scheduling.model import Downtime
from planning.tests.scheduling.factory import at, job, machine, make_input, op
from planning.validation import validate

ROUTES = [
    [("WC-10", "turning", 6), ("WC-22", "cladding", 4), ("WC-30", "lapping", 2), ("WC-35", "assembly", 3),
     ("WC-40", "hydro", 2), ("WC-30", "final-inspection", 1)],
    [("WC-10", "turning", 4), ("WC-35", "assembly", 2), ("WC-40", "hydro", 1.5), ("WC-50", "epoxy", 3),
     ("WC-30", "final-inspection", 1)],
    [("WC-14", "vtl", 10), ("WC-22", "saw", 8), ("WC-40", "radiography", 4), ("WC-40", "hydro", 3),
     ("WC-30", "final-inspection", 1)],
]
MACHINES = [machine("MC-1", "WC-10", "turning"), machine("MC-2", "WC-10", "turning"), machine("MC-3", "WC-10", "turning", factor=0.8),
            machine("VT-1", "WC-14", "vtl"), machine("WL-1", "WC-22", "cladding"), machine("WL-2", "WC-22", "saw"),
            machine("SA-1", "WC-30", "lapping", "final-inspection"), machine("SA-2", "WC-30", "final-inspection"),
            machine("FA-1", "WC-35", "assembly"), machine("TE-1", "WC-40", "hydro", "radiography"),
            machine("TE-2", "WC-40", "hydro"), machine("SF-1", "WC-50", "epoxy")]


def big_plant(lines: int = 48, seed: int = 11, time_limit: float = 10.0):
    rnd = random.Random(seed)
    jobs = []
    for i in range(lines):
        route = ROUTES[i % len(ROUTES)]
        ops = []
        for k, (wc, cap, hours) in enumerate(route):
            ops.append(op(f"OP-{(k + 1) * 10:03d}", wc, hours * rnd.uniform(0.6, 1.4), cap,
                          preds=(f"OP-{k * 10:03d}",) if k else (), gate=cap in ("hydro", "final-inspection")))
        ready = {"OP-010": at(rnd.randint(0, 6))} if rnd.random() < 0.4 else {}
        jobs.append(job(f"SO-{2000 + i}", ops, due_day=rnd.randint(6, 28),
                        priority=rnd.choice(["Critical", "High", "Normal", "Normal", "Low"]), material_ready=ready))
    downtime = [Downtime("MC-1", at(2, 6), at(2, 22), "Confirmed", "PM"), Downtime("TE-1", at(8, 6), at(9, 14), "Confirmed", "Overhaul")]
    return make_input(MACHINES, jobs, downtime=downtime, horizon_days=35, time_limit_s=time_limit)


@pytest.fixture(scope="module")
def solved():
    inp = big_plant()
    t0 = time.perf_counter()
    sched = solve(inp)
    elapsed = time.perf_counter() - t0
    t1 = time.perf_counter()
    report = validate(sched)
    return inp, sched, report, elapsed, time.perf_counter() - t1


def test_large_plant_is_scheduled_within_the_time_budget(solved):
    inp, sched, report, elapsed, _ = solved
    ops = sum(len(j.ops) for j in inp.jobs)
    assert ops >= 250 and len(inp.jobs) == 48
    assert sched.outcome in ("Feasible", "Feasible with lateness"), sched.conflicts
    assert len(sched.ops) == ops                                          # every operation placed
    assert elapsed < inp.options.time_limit_s + 20, f"solve took {elapsed:.1f}s"   # limit + model build/explain


def test_large_plant_schedule_passes_independent_validation_quickly(solved):
    _, _, report, _, validate_seconds = solved
    assert report.passed, report.violations[:5]
    assert validate_seconds < 15, f"validation took {validate_seconds:.1f}s"


def test_time_limit_never_loses_the_schedule():
    """Even with an unrealistically short limit the greedy schedule is returned — feasible and validated."""
    inp = big_plant(lines=30, seed=3, time_limit=0.05)
    sched = solve(inp)
    assert sched.outcome in ("Feasible", "Feasible with lateness")
    assert sched.solver_status in ("FEASIBLE", "OPTIMAL", "GREEDY (CP-SAT time limit)")
    assert validate(sched).passed
