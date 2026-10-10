# M10 — Live workbench & what-if scenarios

| | |
|---|---|
| **Date** | 2026-10-10 |
| **Author** | Khaled (implemented with Claude Code) |
| **Status** | Done |
| **Commits** | _see git log — "M10" commit_ |
| **Spec sections** | §10 (what-if simulation), §12.6 (planning workbench), §6.18–6.20 (runs, versions, alerts), acceptance test 9 |

## Goal
Planners try changes without touching the approved schedule, see exactly what each change does and why, and the
system flags the baseline when reality (a supplier receipt) moves under it.

## What was done

**Scenario engine — [planning/scenarios.py](../../planning/scenarios.py)**
- Seven change types covering all spec §10 examples: supplier receipt moves (later *or earlier*), machine
  unavailable, urgent order, order quantity change, overtime for a work center, second machine, order priority.
  Changes are applied to a **copy** of one snapshot; invalid changes come back as plain messages.
- Each run plans **today's data and the scenario on the same snapshot** (MRP + CP-SAT + validation), and the
  scenario solve is **anchored** to the reference plan (moving an operation costs more than finishing earlier
  gains), so only what the scenario forces moves — no solver noise in the comparison (D-032).
- Comparison (spec §10): delivery-date changes per line (today vs scenario vs approved baseline, with reasons tied
  to the change that caused them and the scenario's limiting factors), new and resolved material shortages,
  utilisation per work center and bottleneck, additional overtime hours, late/blocked/lateness totals.
- Saved as a separate version: run type `simulation`, assumptions (name, changes, `what_if_only`) on the run.

**Database** — migrations `20261010154502_scenarios`, `20261010155333_baseline_impact_multilevel`:
- `record_schedule_run()` stores run type and assumptions; operations on a hypothetical machine are stored without
  a machine and labelled `Scenario machine …` (the database re-check accepts them).
- The approval guard refuses Proposed/Approved for **what-if-only** versions (delay, urgent order, quantity,
  extra machine) — "apply the change to the plant data and re-run" (D-031). Decision scenarios (overtime, planned
  outage, priority) can be submitted.
- **Acceptance test 9:** a trigger on `purchase_order_items.expected_delivery_date` flags the approved baseline
  with a critical `late_supplier_receipt` alert when a receipt moves later than a baseline operation that consumes
  the item — following the BOM upwards through sub-assemblies (ACT-DA-400 → AMK-TBV-10 → OP-070 of the valve),
  minus sub-assembly lead times. SECURITY DEFINER (purchasing can move dates but not write alerts). The baseline
  itself never changes.

**App**
- **What-If Scenarios** page (`/scenarios`, nav): builder with one optional section per change type, background
  run (two solves) with polling, saved scenarios list, baseline-impact banner.
- Scenario **comparison page** (`/scenarios/vX`): changes, today → scenario KPIs, delivery changes with reasons,
  shortages, utilisation.
- Workbench: comparison strip computed from both versions' data with a selector for any version (hard-coded
  v4.2/v4.3 story removed); "Simulate Scenario" opens the builder; baseline-impact banner; scenario versions show
  their outage/overtime blocks and a lane for a hypothetical second machine.
- CLI: `python -m planning.cli scenario --delay PO-8821:5 [--down …] [--urgent …] [--qty …] [--overtime …]
  [--extra-machine …] [--priority …] [--save]` (read-only unless `--save`).

**Results on the demo data**
| Scenario | Effect |
|---|---|
| PO-8821 +5 d (roadmap check) | Only SO-1005/2 changes: on time → late +61.5 h, "earliest possible Dec 03 13:30", reason PO-8821/1 → Dec 03 |
| Urgent Critical 6 × V-300-CGV-04 by Nov 21 | SIM-1 on time; SO-1019 now blocked (its components go to the Critical order: 4 new shortages); SO-1020 +36.5 h |
| MC-014 down Nov 17–20 + Saturday OT + SO-1012 Critical | No operation on MC-014 in the outage; WC-10 load changes; decision scenario (can be submitted) |
Live: scenario run #1152 → **v4.5** (what-if only, kept in the dev DB); submitting it is refused by the database.

**Tests** (247 passing: 156 app/engine + 91 DB)
- [tests/test_scenarios.py](../../tests/test_scenarios.py) — gate: acceptance test 9 offline (baseline/snapshot
  untouched, only the affected line changes, reasons), urgent order, outage + decisions, quantity/extra machine,
  invalid changes, pages, workbench comparison from data, background job, errors.
- [tests/db/test_scenarios_db.py](../../tests/db/test_scenarios_db.py) — receipt change flags the baseline
  (multi-level), earlier receipt does not, what-if version refused, decision scenario submitted, hypothetical
  machine stored, unknown run type refused, CLI.

## Decisions
- D-031 What-if-only scenarios can never be approved; decision scenarios can.
- D-032 Scenarios are compared with today's data planned in the same run, anchored to it.
- D-033 Receipt changes flag the baseline in the database, through the BOM.

## How to verify (run 2026-10-10)
```powershell
.\.venv\Scripts\python -m pytest tests/test_scenarios.py           # 17 passed (acceptance test 9 + scenarios)
.\.venv\Scripts\python -m pytest                                   # 247 passed
.\.venv\Scripts\python -m planning.cli scenario --delay PO-8821:5  # only SO-1005/2 changes; baseline untouched
```
Live (`DATA_BACKEND=supabase`, planner): What-If Scenarios → PO-8821, +5 → Run → "saved as v4.N (what-if only)"
→ Open comparison. Submitting it in Approvals is refused; the baseline stays v4.2. As purchasing, moving a PO-8821
date later adds a critical "approved baseline … starts PRD-1005-1 OP-070" alert and a banner on the workbench.

## Known gaps / follow-ups
- One change of each type per scenario (the engine accepts lists; the form shows one of each).
- Scenario overtime has no cost model yet (hours are reported, `overtime_cost` stays 0).
- The receipt-change flag compares with planned operation starts of the baseline; it does not re-run MRP
  allocation (the next MRP/solver run gives the full picture).
- Hypothetical machines are not saved as machines; their operations are labelled on the version only.
- The dashboard's capacity and risk widgets still read the baseline's stored metrics (the seeded 4.2 numbers until
  a solver-made version is approved).

## Problems hit
- Two independent time-limited solves differed by hours on untouched lines → anchored scenario solve.
- The first receipt trigger matched direct components only and missed the roadmap example (actuators go into a
  kit) → recursive BOM walk with lead times.
- A refused submit aborts the test transaction; assertions moved before it.
