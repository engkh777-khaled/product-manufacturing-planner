# M08 — Material requirements planning engine

| | |
|---|---|
| **Date** | 2026-10-10 |
| **Author** | Khaled (implemented with Claude Code) |
| **Status** | Done |
| **Commits** | `d40a04e` |
| **Spec sections** | §7.1–7.5 (explosion, netting, time-phased availability, shortage alerts, corrective actions), §6.11 (eligible receipts), §6.18/§6.20 (runs, alerts), acceptance tests 2, 3, 8, 10 |

## Goal
Correct, explainable, time-phased material shortages computed from real BOMs, stock and purchase orders —
independent of the AI and of the web app.

## What was done

**`planning/` package — pure Python, no web or database code** ([planning/mrp/](../../planning/mrp/))
- [inputs.py](../../planning/mrp/inputs.py) — builds the MRP input from one `planning_snapshot()` document (one MVCC
  snapshot, spec §4). Approved BOMs only. **Eligible supply:** net-available stock (on hand − reserved − QC hold) at
  as-of; open PO lines that are not cancelled/rejected and have an expected date (+ optional inspection days).
  QC-hold stock and undated receipts are recorded for alerts, not counted. **Need dates:** start of the consuming
  operation in the approved **baseline schedule** (`bom_items.operation_reference`), else the production-order start,
  else requested date − standard lead time; never before as-of.
- [engine.py](../../planning/mrp/engine.py) — **level-by-level MRP**: explode one BOM level for all order lines,
  allocate every requirement of that level **across orders by priority, then need date** (earliest-available supply
  first; each unit allocated once), then explode only the unmet quantity of manufactured sub-assemblies (netting at
  every level, children needed the sub-assembly lead time earlier); readiness computed bottom-up. Scrap applied once
  (gross = qty × qty/parent ÷ (1 − scrap)). `projection()` gives time-phased availability per item.
- [alerts.py](../../planning/mrp/alerts.py) — spec §7.4 alerts per order line: `material_shortage`,
  `late_supplier_receipt`, `quality_hold`, `missing_bom`, `missing_routing`, undated receipts — each with order,
  component, required / available / shortage, required and expected dates, impact (operation that cannot start) and
  corrective actions (release QC hold, expedite PO, re-prioritise against the named higher-priority orders,
  reschedule). Sub-assemblies are explained through their children (no double reporting).
- [cli.py](../../planning/cli.py) — `python -m planning.cli mrp [--order …] [--item …] [--all] [--save --as user]`.

**Database** — `20261010141344_record_mrp_run`: `record_mrp_run(p jsonb)` (SECURITY INVOKER, planner/admin) stores
the run (`run_type = 'mrp'`, input summary, algorithm version, outcome, seconds, `created_by`), resolves open material
alerts of earlier runs and inserts the new ones linked to the order lines — atomically. Seed: V-900 BOM lines got
their consuming operations (studs at OP-050).

**App**
- Supabase datasets run MRP on every snapshot (milliseconds, cached with it): order **material-ready dates**,
  allocation-aware **staging** in the order inspector ("Short 3 · QC hold", "Late · ready Nov 25"), dashboard
  shortage KPI from MRP.
- Inventory & Purchasing: **Material Requirements (MRP)** section — outcome, short/late/covered counts, last saved
  run, requirements table (problems or all), time-phased availability per problem item, and
  **Run MRP & update alerts** (planner/admin) which saves the run through `record_mrp_run`.

**Results on the demo data** (as of 2025-11-15)
| Order | Finding |
|---|---|
| SO-1005 (Critical) | Seat rings: 10 free, line 2 needs 4.21 on Nov 17, line 1 needs 8.42 at OP-030 on Nov 19 → line 1 short 3; 12 on QC hold could cover. Spares line: actuators needed Nov 17, PO-8821 Nov 28 (+11 d) |
| SO-1012 | Inconel studs needed at OP-050 on Nov 24, PO-8815 arrives Nov 25 (+1 d) |
| SO-1022 (Low) | Bodies, ball and seat rings short — stock allocated to higher-priority SO-1005 (stated in the alert) |
| SO-1021 | Needs data: no approved BOM |

**Tests**
- [planning/tests/test_mrp.py](../../planning/tests/test_mrp.py) — 15 pure tests: scrap, late/on-time receipts, need
  dates, priority vs need date and no double allocation, sub-assembly netting and lead time, short children, missing
  BOM, QC hold, cancelled/rejected/undated receipts, and **golden acceptance tests 2, 3, 8, 10** on the recorded
  snapshot, projection, payload.
- [tests/test_mrp_app.py](../../tests/test_mrp_app.py) — 4 offline app tests; [tests/db/test_mrp_db.py](../../tests/db/test_mrp_db.py)
  — 3 DB tests (record as planner, alerts replaced and linked, stores refused, CLI end-to-end).

## Decisions
- D-025 Level-by-level MRP with priority-then-need-date allocation.
- D-026 Need dates from the baseline schedule; MRP owns the material alert types.

## How to verify (run 2026-10-10)
```powershell
.\.venv\Scripts\python -m pytest                       # 167 passed
.\.venv\Scripts\python -m planning.cli mrp              # problems from the dev database
.\.venv\Scripts\python -m planning.cli mrp --item SR-ST6-10
```
Live (DATA_BACKEND=supabase): planner → Inventory → *Run MRP & update alerts* → "MRP run #1102 saved with 10 alerts";
dashboard risk queue now includes SO-1022; SO-1022 inspector shows the shortages and names SO-1005 as the
higher-priority consumer; the stores user has no Run button. **Run #1102 was kept in the dev database** (computed
alerts replace the seeded placeholders); after `supabase db reset --linked`, click *Run MRP* again.

## Known gaps / follow-ups
- Infinite capacity: need dates come from the current baseline; M09's scheduler will feed material-ready dates
  back as constraints (an operation may not start before its material).
- No issue/consumption tracking: in-production orders still count their full component demand (no WIP issues yet).
- Fractional allocation (expected scrap) — shortages of discrete parts are rounded up when reported.
- Allocation rule is fixed (priority, then need date); make it configurable when business rules are agreed.
- No warehouse transfers or alternative components (spec §7.5 lists them as possible actions).

## Problems hit
- First version allocated order-by-order: line 1 took stock needed earlier by line 2 of the same order → switched
  to level-by-level allocation across orders (troubleshooting).
- A synthetic test added an order with an earlier need that legitimately took the supply — the test was wrong.
