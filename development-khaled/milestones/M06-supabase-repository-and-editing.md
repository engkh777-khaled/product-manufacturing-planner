# M06 — Supabase repository & master-data editing

| | |
|---|---|
| **Date** | 2026-10-10 |
| **Author** | Khaled (implemented with Claude Code) |
| **Status** | Done |
| **Commits** | _see git log — "M06" commit_ |
| **Spec sections** | §4 (consistent snapshot), §5 (role permissions), §6.1/§6.7 (orderable products, engineering config), §12.2–12.4 |

## Goal
Run every page on the Supabase database, filtered by the signed-in user's RLS, and let users edit the data their
role owns: draft sales orders, inventory, purchase order lines and BOM revisions.

## What was done

**Read path — one consistent snapshot per request**
- `public.planning_snapshot()` (SECURITY INVOKER) returns all 22 data sets the caller may see, in **one statement =
  one MVCC snapshot** (spec §4). ~170 KB for the demo data, ~0.5 s from the dev machine.
- [app/data/supabase_dataset.py](../../app/data/supabase_dataset.py) converts it into the same models as
  `mock_data` (multilevel BOM trees from chained single-level BOMs, routings with precedence, schedule versions with
  operations / downtime / scenario overtime / material markers, purchasing rows, approval requests). Display fields
  are derived from data: limiting constraint and diagnostics from planning alerts, projected completion from the
  baseline production orders, staging coverage from BOM × inventory, "shortage blocked" from
  `bom_items.operation_reference` + late supplier alerts. Times are converted to plant time (America/Chicago).
- [app/data/dataset.py](../../app/data/dataset.py): per-request dataset (context variable). The middleware installs the
  Supabase dataset when `DATA_BACKEND=supabase`; otherwise `mock_data`. Snapshots are cached 30 s per token and
  dropped after every write. **If Supabase is unreachable the app returns 503 — it never silently shows demo data.**
- Repository/routes refactored to read through `ds()`; hard-coded v4.2/v4.3 replaced by *baseline* / *latest scenario*.
- Config rule: `DATA_BACKEND=supabase` requires `AUTH_MODE=supabase` (data is read as the user).

**Write path — as the signed-in user** ([app/data/writes.py](../../app/data/writes.py))
- All writes go through PostgREST with the user's token; RLS is the final authority. Database errors become
  readable messages ("You don't have permission…", "Rejected: <rule>").
- Atomic database functions (SECURITY INVOKER): `save_draft_sales_order(jsonb)` (create / replace a Draft order and
  its lines in one transaction; only Active finished goods; approved BOM/routing attached automatically),
  `confirm_sales_order(text)`, `approve_bom_revision(uuid)` (Under Review → Approved, previous → Superseded).
- Approval decisions (APR-…) and audit entries are now persisted in Supabase mode.

**Screens**
- Sales Orders: *Create Customer Sales Order* form (`/orders/new`), *Edit Draft*, *Confirm*, *Delete* (Draft only).
  Server-side validation + database checks. Enabled for planner/admin in Supabase mode.
- **New: Inventory & Purchasing** (`/inventory`, spec §12.4): incoming PO lines with slippage, inventory balances
  (on hand / reserved / QC hold / net available), shortage KPIs. Inline *Adjust* (stores/admin) and *Update*
  (purchasing/stores/admin). Read-only on mock data.
- Engineering: **BOM revision history** with *Submit for review* (engineering), *Approve* / *Return to draft* (approver).

**Data (dev project + seed.sql)**
- BOM lines record the consuming operation (`operation_reference`).
- Schedule versions carry `metrics.work_center_load` and `metrics.machine_utilization` (labelled seed values until
  the solver computes them in M09); alert details carry MRP `required_date`s.
- Inventory topped up where the scrap allowance made extra components short — only the story's three shortages
  remain (ACT-DA-400, SR-ST6-10, ST-IN718-M24).

**Migrations**: `20261010103548_snapshot_and_bom_approval`, `20261010104424_sales_order_editing`.

**Tests**
- [tests/test_supabase_dataset.py](../../tests/test_supabase_dataset.py) — 16 offline tests on a recorded snapshot
  ([tests/fixtures/snapshot.json](../../tests/fixtures/snapshot.json), fictional): derived fields, multilevel BOM,
  capacity, purchasing, Gantt markers, every page rendering from DB-shaped data, form validation.
- [tests/db/test_writes.py](../../tests/db/test_writes.py) — 7 live tests as real demo users; each restores the data.

## Decisions
- D-020 One RLS-filtered snapshot per request, converted to the existing models.
- D-021 Multi-row writes are SECURITY INVOKER database functions.
- D-022 Capacity loads are stored as planning-run outputs on schedule versions.

## How to verify (run 2026-10-10)
```powershell
.\.venv\Scripts\python -m pytest            # 126 passed (65 app + 61 DB)
$env:DATA_BACKEND="supabase"; .\.venv\Scripts\uvicorn app.main:app --reload
```
Live check (DATA_BACKEND=supabase): planner signs in → all 6 pages 200 from the database; creates SO-1023 through the
form → visible as Draft with Edit/Confirm/Delete → deleted. Stores user → 39 *Adjust* and 5 *Update* controls on
`/inventory`, `/orders/new` → 403. First page ~1.3 s (snapshot), next pages ~0.05 s (cache).
Supabase security advisor: only *Leaked password protection disabled* (Auth setting, see gaps).

## Known gaps / follow-ups
- **Auth settings for the owner** (dashboard): disable public sign-ups; enable *leaked password protection*
  (Authentication → Policies/Passwords; may require a paid plan).
- Field-level limits inside a role are app-side only (e.g. stores can technically also change a PO line's expected
  date — RLS works per row/role, not per column).
- Approving a plan sets the request status only; switching the baseline schedule version comes with M07.
- Capacity numbers and copilot texts are still seeded / static until M09 (solver) and M11 (AI).
- Snapshot size grows with data; fine for the MVP, revisit (pagination / targeted RPCs) before real factory volumes.

## Problems hit
- The scrap allowance (yield) made 11 components "short" with the seed stock levels → realistic arithmetic, seed adjusted.
- `zoneinfo` on Windows needs the `tzdata` package (added to requirements).
- A bash heredoc mixing quotes broke an inline edit script → write helper scripts to files instead.
