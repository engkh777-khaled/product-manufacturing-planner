"""Background scheduler runs (M09, spec §8.5).

Clicking "Re-run CP-SAT" starts a job: a thread fetches a fresh RLS snapshot as the user, hands it to a worker
process (CP-SAT is CPU-bound and must not block web requests), and saves the result through
`record_schedule_run()` with the user's token — so RLS and the database re-check apply as for any write. The page
polls the job. Jobs live in memory: one app instance (multi-instance needs a queue — M12).
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.config import get_settings
from app.data import writes

log = logging.getLogger(__name__)
_jobs: dict[str, "ScheduleJob"] = {}
_lock = threading.Lock()
_pool: ProcessPoolExecutor | None = None
MAX_KEPT = 50


@dataclass
class ScheduleJob:
    id: str
    user_id: str
    user_name: str
    started_at: datetime
    options: dict
    kind: str = "schedule"              # schedule | scenario (M10)
    changes: list[dict] = field(default_factory=list)
    name: str = ""
    comparison: dict = field(default_factory=dict)
    status: str = "running"             # running | done | failed
    stage: str = "Reading plant snapshot"
    message: str = ""
    outcome: str | None = None
    version_number: str | None = None
    run_number: int | None = None
    validation_passed: bool | None = None
    seconds: float | None = None
    lines: list[dict] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    finished_at: datetime | None = None

    @property
    def elapsed(self) -> float:
        end = self.finished_at or datetime.now(timezone.utc)
        return (end - self.started_at).total_seconds()


def _solve(job: "ScheduleJob", snap: dict) -> dict:
    global _pool
    from planning.scenarios import scenario_payload
    from planning.scheduling import plan_payload
    fn, args = ((scenario_payload, (snap, job.changes, job.name, None, job.options)) if job.kind == "scenario"
                else (plan_payload, (snap, None, job.options)))
    if get_settings().scheduler_worker == "thread":
        return fn(*args)
    with _lock:
        if _pool is None:
            _pool = ProcessPoolExecutor(max_workers=1)
    return _pool.submit(fn, *args).result(timeout=600)


def _run(job: ScheduleJob, token: str) -> None:
    try:
        snap = writes.fetch_snapshot(token)
        if not snap.ok:
            raise RuntimeError(snap.message)
        job.stage = ("Planning today's data and the scenario" if job.kind == "scenario"
                     else "Solving with CP-SAT and validating")
        t0 = time.perf_counter()
        body = _solve(job, snap.data)
        if "error" in body:
            raise RuntimeError(body["error"])
        job.seconds = round(time.perf_counter() - t0, 1)
        job.outcome = body["status"]
        job.lines = body["version"]["metrics"].get("lines", [])
        job.conflicts = body["version"]["metrics"].get("conflicts", [])
        job.comparison = body["version"]["metrics"].get("comparison", {})
        job.stage = "Saving draft version"
        saved = writes.record_schedule_run(body, token=token)
        if not saved.ok:
            raise RuntimeError(saved.message)
        data = saved.data or {}
        job.run_number, job.version_number = data.get("run_number"), data.get("version_number")
        job.validation_passed = data.get("validation_passed")
        job.message, job.status = saved.message, "done"
    except Exception as exc:  # report, never crash the web process
        log.exception("schedule job %s failed", job.id)
        job.status, job.message = "failed", f"{'Scenario' if job.kind == 'scenario' else 'Solver'} run failed: {exc}"
    finally:
        job.finished_at = datetime.now(timezone.utc)


def start(token: str, user_id: str, user_name: str, options: dict | None = None, kind: str = "schedule",
          changes: list[dict] | None = None, name: str = "") -> ScheduleJob:
    """Start a run unless this user already has one running (then return that one)."""
    with _lock:
        running = next((j for j in _jobs.values() if j.user_id == user_id and j.status == "running"), None)
        if running:
            return running
        if len(_jobs) >= MAX_KEPT:
            for old in sorted(_jobs.values(), key=lambda j: j.started_at)[: len(_jobs) - MAX_KEPT + 1]:
                if old.status != "running":
                    _jobs.pop(old.id, None)
        opts = {"time_limit_s": get_settings().scheduler_time_limit_seconds, **(options or {})}
        job = ScheduleJob(id=uuid.uuid4().hex[:12], user_id=user_id, user_name=user_name,
                          started_at=datetime.now(timezone.utc), options=opts, kind=kind,
                          changes=list(changes or []), name=name)
        _jobs[job.id] = job
    threading.Thread(target=_run, args=(job, token), name=f"schedule-{job.id}", daemon=True).start()
    return job


def get(job_id: str, user_id: str) -> ScheduleJob | None:
    job = _jobs.get(job_id)
    return job if job and job.user_id == user_id else None
