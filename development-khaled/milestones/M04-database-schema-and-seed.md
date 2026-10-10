# M04 — Database schema, constraints & seed data

| | |
|---|---|
| **Date** | 2026-10-10 |
| **Author** | Khaled (implemented with Claude Code) |
| **Status** | Done |
| **Commits** | _see git log — "M04" commit_ |
| **Spec sections** | §6 (all 22 tables), §6.3 cycle detection, §6.7 frozen engineering config, §6.9 net available, §6.19 baseline, §13 demo data |

## Goal
The spec §6 data model exists in the Supabase dev project as versioned migrations, loaded with the fictional
dataset, with the business rules enforced by the database itself.

## What was done

**Supabase project files**
- `npx supabase init` → [supabase/config.toml](../../supabase/config.toml) (`project_id = "product-manufacturing-planner"`).
- 6 migrations in [supabase/migrations/](../../supabase/migrations/), applied to the dev project. File timestamps
  match the remote migration history, so `supabase db push` sees them as already applied.

| Migration | Tables |
|---|---|
| `20261010091033_helpers` | `set_updated_at()` trigger function |
| `20261010091050_capacity_and_calendars` | work_centers, machines, shift_calendars, calendar_exceptions, machine_downtime |
| `20261010091118_engineering` | products, bom_headers, bom_items, routing_headers, routing_operations, routing_operation_dependencies |
| `20261010091134_inventory_and_purchasing` | warehouses, inventory_balances, purchase_orders, purchase_order_items |
| `20261010091149_demand` | sales_orders, sales_order_items |
| `20261010091217_planning` | planning_runs, schedule_versions, production_orders, production_operations, planning_alerts, approval_requests, audit_logs |

**Rules enforced in the database** (not just the UI)
- Unique business codes; `CHECK` constraints for positive quantities, scrap in [0, 1), status/priority value lists,
  date ranges, `reserved + quality hold ≤ on hand`, no over-receipt on POs.
- One **Approved** BOM and routing per product (partial unique indexes).
- **BOM cycle detection** trigger walks the multilevel structure (spec §6.3).
- Operation dependencies must stay inside one routing (trigger).
- A sales line's BOM/routing revision must belong to its product, and becomes **frozen** once the line is
  Planned / In Production / Completed (spec §6.7).
- `inventory_balances.net_available_quantity` and `purchase_order_items.open_quantity` are generated columns.
- **Exactly one baseline** schedule version; a baseline must be Approved; an unvalidated version can't be Approved;
  production orders/operations are stored **per version**, so scenarios never overwrite the baseline (§6.19, §10).
- Approval decisions require `decided_at`; one pending request per version.
- **Row Level Security enabled on all 24 tables with no policies** → the publishable key (anon/authenticated)
  can read nothing until M05 adds role policies. Server roles (postgres/service) are unaffected.

**Seed data** — [supabase/seed.sql](../../supabase/seed.sql), all fictional:
7 work centers · 14 machines · 2 shifts × 7 WCs · 2 holidays · 3 downtime events · 9 finished goods + 40 components ·
12 BOM revisions (V-600 Rev B superseded → Rev C approved; V-800 Draft) · 46 BOM lines · 8 routings / 39 operations ·
3 warehouses · 39 inventory balances · 4 POs · 10 sales orders / 13 lines · runs 1038 & 1042 ·
versions 4.2 (baseline) & 4.3 (proposed) · 24 production orders · 58 operations · 8 alerts · approval APR-1042.

**Tests** — [tests/db/](../../tests/db/): 37 pytest tests (schema + RLS present, seed counts, 16 constraint
violations, cycle detection, net available, baseline preserved, default-deny for anon/authenticated). Each test
runs in a rolled-back transaction. Skipped automatically when `DATABASE_URL` is not set (CI).

## Decisions
- D-015 Schema conventions (components are products; multilevel BOM = chain of single-level BOMs; text + CHECK
  instead of enum types; production orders per schedule version; RLS default-deny).
- D-016 Migrations applied through the Supabase API now, file names aligned to remote versions.

## How to verify
Run on 2026-10-10 against the dev project (via the Supabase API, as the DB password was not yet set):
- Row counts: 24/24 tables match the expected numbers above.
- 19 rule checks: every invalid write rejected with the expected error; anon/authenticated see 0 rows.
- Supabase advisors: **no security or performance warnings**. Only INFO notices: "RLS enabled, no policy"
  (expected until M05) and "unused index" (new database).
- App suite: `pytest` → 35 passed, 37 skipped (DB tests without `DATABASE_URL`).

Developer gate with `DATABASE_URL` (Session pooler) in `.env` — **run 2026-10-10: 37 passed**; full suite **72 passed**:
```powershell
.\.venv\Scripts\python -m pytest tests/db -v     # 37 passed
.\.venv\Scripts\python -m pytest                 # 72 passed (35 app + 37 DB)
```
Full rebuild of the dev database from files (wipes it):
```powershell
npx supabase link --project-ref qemoliyeblfmrccniczr
npx supabase db reset --linked
```

## Known gaps / follow-ups
- No RLS policies yet (M05). `approved_by` / `created_by` columns are empty until users exist (M05).
- Audit triggers and append-only protection come in M07.
- The UI still reads the in-memory mock (until M06). Where the seed differs from the mock it is the seed that is
  authoritative: SO-1012 uses new product `V-900-MSB-14`; some operation numbers were renamed to fit real routings
  (e.g. SO-1012 radiography `OP-055`, SO-1015 blast/coat `OP-085`/`OP-088`, SO-1008 lapping `OP-055`); SO-1019
  `OP-045` now starts after `OP-025` finishes (the mock overlapped them — a precedence violation).
- Manufactured components without their own BOM (e.g. `BL-304-08`) are treated as stocked items for now.

## Problems hit
- First `DATABASE_URL` attempt: missing `DATABASE_URL=` prefix, placeholder brackets kept, `@`/`#` in the password not
  percent-encoded; then "password authentication failed" → password reset with a generated (alphanumeric) one.
- pytest's long tracebacks printed the decoded connection string → added `pytest.ini` (`--tb=short`) and a
  sanitized connection-failure message in `tests/db/conftest.py`. Connection now uses `sslmode=require`.
- Test used `AT TIME ZONE '-06'` (POSIX sign is inverted = UTC+6) → now `'America/Chicago'`.
- Two of my first SQL checks used `revision = 'Rev C'`, which matched two products; fixed by filtering on product.
