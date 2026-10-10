# M09 — Finite-capacity scheduler & independent validator

| | |
|---|---|
| **Date** | 2026-10-10 |
| **Author** | Khaled (implemented with Claude Code) |
| **Status** | Done |
| **Commits** | _see git log — "M09" commit_ |
| **Spec sections** | §8.1–8.8 (inputs, durations, hard constraints, objective, CP-SAT, horizon, outcomes, validation), §6.12–6.19, §12.5 (Resources & Calendars), acceptance tests 1, 4, 5, 6, 7, 8 (+ 2, 3 on the plant data) |

## Goal
Feasible, explainable production schedules from OR-Tools CP-SAT that respect machines, calendars, downtime, material
readiness and commitments — checked by a validator that shares no code with the solver, and that the database
re-checks before anything can be approved.

## What was done

**Scheduler — `planning/scheduling/`** (pure Python + OR-Tools 9.15)
- [model.py](../../planning/scheduling/model.py) — machines, shifts, calendar exceptions, downtime, routing
  operations, jobs (one production order per sales order line), options and objective weights. Duration rule
  (spec §8.2, D-027): setup + inspection once per production order; cycle × quantity, or × batches of
  `maximum_batch_quantity` for `per_batch` operations; ÷ machine speed factor.
- [calendar.py](../../planning/scheduling/calendar.py) — per-machine working windows: shifts (plant-local wall
  clock → UTC per day, so DST is handled) − holidays/shutdowns + **approved** overtime; 30-minute slots; start
  tables compressed into *pieces* (start ∈ [lo, hi] ⇒ finish = start + c).
- [inputs.py](../../planning/scheduling/inputs.py) — from one `planning_snapshot()`: open order lines (drafts only
  on request), approved routing, qualified machines (work center + capability tags), M08 material-ready dates per
  consuming operation (short ⇒ blocked), baseline commitments (completed ⇒ not rescheduled; released / in progress
  / frozen / inside the freeze window ⇒ fixed). Missing BOM, routing, qualified machine or calendar ⇒ *needs data*.
- [solver.py](../../planning/scheduling/solver.py) — greedy list schedule first (feasible by construction; used
  as complete hint, to bound each line's search window, and as proven-feasible fallback), then CP-SAT: one
  optional interval per qualified machine, piece choice per operation, precedence, no-overlap per machine, material
  release; committed work and confirmed downtime are never overlapped; work pauses outside shifts. Objective:
  priority-weighted lateness + late lines + baseline changes + operation end times. Outcomes exactly as spec §8.7;
  `Infeasible` only when proven (or when committed work itself conflicts — reported with the reason).
- [explain.py](../../planning/scheduling/explain.py) — per line: completion, lateness, **earliest possible
  completion** (the line alone on the plant) and limiting factors: material, processing time on the critical
  path, confirmed downtime, committed work, or the bottleneck machine and the competing orders.
- [service.py](../../planning/scheduling/service.py) — snapshot → solve → validate → payload for the database,
  plus work-center/machine loads in the horizon.

**Independent validation — `planning/validation/validator.py`** — no shared code with the solver: durations
recomputed from routing times, working time re-derived from raw shift/exception records by sampling. Checks:
assignments (qualified, active, right work center, each required operation exactly once), mandatory inspections,
precedence, machine overlap, confirmed downtime (planned = warning), calendar (start in working time, enough
working minutes), work-center capacity per day, material readiness, frozen commitments, delivery calculations,
completeness.

**Database** — migration `20261010145735_schedule_runs`:
- `planning_snapshot()` now also returns `shift_calendars` and `calendar_exceptions`.
- `record_schedule_run(p)` (planner/admin, SECURITY INVOKER): planning run + Draft version (next minor number,
  based on the baseline) + production orders/operations, atomically; no version when there is no schedule.
- `private.schedule_hard_violations(version)` — a third, SQL check (overlap, precedence, confirmed downtime,
  qualification, times). Runs on record and **on every move to Proposed/Approved**, so a schedule edited or forged
  behind the app can never be approved.
- `validation_passed` can only be set inside `record_schedule_run()`; editing planned times/machines of a
  Draft/Proposed version clears it (`validation_stale`).

**App**
- **Resources & Calendars** (`/resources`, new nav item): work centers, machines with capabilities and speed,
  routing operations per work center, shifts, holidays/shutdowns/overtime, downtime. Supervisors/admins record
  downtime (plant time), confirm/cancel it, add calendar entries and approve overtime (RLS enforces the same).
- **Re-run CP-SAT** (workbench, planner/admin, Supabase mode) starts a background job: fresh RLS snapshot → solver
  in a **worker process** → `record_schedule_run` as the user. The page polls every second and links the new Draft.
- Workbench **Delivery by Order Line** and **Independent Validation** panels for solver-made versions; the
  comparison strip describes solver drafts from their own data.
- Fix: token verification tolerates 30 s clock skew (users were signed out at random — see troubleshooting).

**Results on the demo data** (horizon Nov 17 – Dec 15 2025, ~10 s)
| Line | Result |
|---|---|
| SO-1005/1 (Critical) | **Blocked** at OP-030: seat rings SR-ST6-10 short 3 — OP-010/OP-020 still scheduled |
| SO-1005/2 | On time Nov 28 — actuation waits for PO-8821 (Nov 28) |
| SO-1012/1 | On time — flange weld OP-050 starts Nov 25 when the PO-8815 studs arrive (test 3) |
| SO-1022/1 (Low) | **Blocked**: bodies, ball, seat rings went to SO-1005 (M08 allocation) |
| others | On time; MC-010 spindle PM and TE-001 overhaul avoided |
Outcome *Feasible with lateness*, validation passed (281 checks). Live: run #1117 → Draft **v4.4** (kept in the
dev DB); submitted as APR-2065 during the check and withdrawn again (v4.4 is Draft).

**Tests** (223 passing: 139 app/engine + 84 DB)
- [planning/tests/scheduling/test_acceptance.py](../../planning/tests/scheduling/test_acceptance.py) — tests 1,
  4 (no overlap, validator catches overlap, alternative machine), 5 (urgent order wins, delivery change vs
  baseline, freeze policy preserved), 6 (downtime avoided; committed work in downtime ⇒ Infeasible with reason),
  7 (impossible date: earliest possible + limiting factors, no fabricated schedule), 8 (needs data), shortages,
  calendars, holidays, overtime, speed factor.
- [test_validator.py](../../planning/tests/scheduling/test_validator.py) — 12 corruption tests, one per check.
- [test_plant.py](../../planning/tests/scheduling/test_plant.py) — golden tests on the recorded snapshot (2, 3).
- [tests/test_scheduling_app.py](../../tests/test_scheduling_app.py) — resources page, roles, plant-time
  conversion, jobs, polling, panels. [tests/db/test_schedule_db.py](../../tests/db/test_schedule_db.py) — record,
  roles, flag guard, invalidation on edit, DB re-check blocks submission, CLI.

## Decisions
- D-027 Scheduling model: 30-min slots, work pauses outside shifts, never overlaps downtime or commitments.
- D-028 Scope: confirmed demand only; short material blocks the operation and its successors.
- D-029 Three independent checks guard approval (validator, database re-check, flag guard).
- D-030 Solver runs as an in-memory background job in a worker process.

## How to verify (run 2026-10-10)
```powershell
.\.venv\Scripts\python -m pytest planning/tests/scheduling        # 36 passed (acceptance 1, 4, 5, 6, 7, 8)
.\.venv\Scripts\python -m pytest                                  # 223 passed
.\.venv\Scripts\python -m planning.cli schedule --horizon 2025-11-17:2025-12-15
.\.venv\Scripts\python -m planning.cli schedule --machine MC-014 --freeze-days 3
```
Live (`DATA_BACKEND=supabase`): planner → Planning Workbench → *Re-run CP-SAT* → "Run #… saved as Draft v4.N,
validation passed" → *Open v4.N* (Gantt + delivery + validation panels). Supervisor → Resources & Calendars →
record a confirmed downtime → re-run: the operation moves. Stores/approver: read-only, cannot run the solver.

## Known gaps / follow-ups
- One pass MRP → schedule: material dates come from the M08 allocation (which uses baseline need dates); the
  scheduler does not re-allocate stock (iterate in M10/M11 if needed).
- Overtime is used freely once approved (no cost term yet); `overtime_cost` metric is 0.
- Shift `capacity_factor` and minimum batch sizes are ignored; batching across orders is not modelled.
- Sub-assemblies have no routings in the seed, so only finished goods are scheduled (sub-assembly lead times come
  from MRP).
- Jobs live in one app process (lost on restart); a multi-instance deployment needs a queue (M12).
- The DB re-check covers overlap/precedence/downtime/qualification only; calendar and material checks are in the
  Python validator — a planner calling the RPC directly with a forged payload could pass those two (approver
  review still applies). Moving the validator server-side is an M12 item.
- The comparison strip and its metrics become data-driven in M10.

## Problems hit
- Element constraints over every slot (~3 800 values) were fully encoded: 80 000 variables, no solution in 10 s →
  piecewise start formulation (2 400 variables) + greedy hint + per-line search window + light presolve: first
  solution in ~1.5 s.
- Random sign-outs during the live check: the PC clock was behind Supabase, so fresh tokens were "not yet valid"
  → 30 s leeway.
- Seed-count DB tests assumed nothing but the seed exists → scoped to the seeded runs/versions.
