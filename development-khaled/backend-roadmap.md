# Backend Roadmap — Supabase + Python

_Written 2026-10-10 · Updated 2026-10-10: **cloud-first** (D-014), M03–M09 done_

Each milestone ends with a **test gate**: commands any developer can run on their own machine to prove the
milestone works. A milestone is not done until its gate passes and its file exists in [milestones/](milestones/).

Order follows spec §15 / §19: data foundation → material planning → scheduling → workbench → AI → hardening.

---

## Language recommendation: Python (keep it)

| Need | Python | Alternatives |
|---|---|---|
| Finite-capacity scheduler (spec §8.5) | **OR-Tools CP-SAT** has its most complete, best-documented API in Python | Also C++/Java/C#. No benefit, and it would split the codebase |
| Data prep for MRP (spec §7) | pandas / plain Python | — |
| Web/API layer | FastAPI, already used by the frontend | Node/TS works but means two languages |
| Supabase | `supabase-py` (Auth, PostgREST), `psycopg` for direct SQL | TS client is more mature, but Python covers everything needed |
| AI tool calling (spec §11) | Official Python SDKs | Same in TS |

**Verdict:** Python. Performance is not a reason to switch, because CP-SAT runs in native C++ under every language
binding. One real constraint is that solver runs are CPU-bound and long. Run them **outside the web request**
(separate process or worker, see M09) so the UI stays responsive. If the Gantt later needs drag-to-reschedule, add a small
JavaScript component rather than changing the backend language.

## Target architecture

```
Browser ──> FastAPI app (UI pages + JSON API)               ← one Python codebase
              │  user's Supabase JWT in an HTTP-only cookie
              ├──> Supabase cloud dev project (later: optional local Docker stack, separate prod project)
              │      Postgres + RLS · Auth · migrations in supabase/migrations
              ├──> planning/ package (pure Python, no web/DB imports)
              │      mrp/ · scheduling/ (OR-Tools) · validation/
              │      run by a worker process; reads a consistent snapshot, writes planning_runs/schedule_versions
              └──> assistant/ (LLM tool calling; tools = repository + planning functions, user-scoped)
```

Rules carried over from the spec:
- The browser never sees the service-role key (§5). The app acts **as the logged-in user** so RLS applies.
- The planning package is deterministic and testable without a database (§18 "Deterministic calculations first").
- Every planning run uses one consistent data snapshot (§4) and produces a new version. Never overwrite a baseline (§6.19).

## Environment: Supabase cloud dev project (D-014)

| Item | Value |
|---|---|
| Project | `product-manufacturing-planner-dev` · ref `qemoliyeblfmrccniczr` · region eu-west-3 (Paris) · free plan |
| API URL | https://qemoliyeblfmrccniczr.supabase.co |
| Dashboard | https://supabase.com/dashboard/project/qemoliyeblfmrccniczr |
| App config | `.env` (git-ignored), template in `.env.example` |

"Test locally" means: the app and all tests run on your machine, against this shared **dev** project.
It is disposable — wiping it is allowed — so never put real factory data in it.

| Tool | How | Used for |
|---|---|---|
| Supabase CLI | `npx supabase …` (Node is installed) | `migration new`, `link`, `db push`, `db reset --linked` |
| Python 3.13 + `.venv` | already set up | App, planning engine, pytest (including DB tests via `DATABASE_URL`) |
| Docker Desktop | **not needed now** | Optional later for a private local stack (`npx supabase start`) |

Secrets you must get from the dashboard (they are not stored anywhere else):
- **Database password** — Project Settings → Database → *Reset database password*. Needed for `npx supabase link` / `db push` and `DATABASE_URL` (M04).
- **Secret API key** — Project Settings → API Keys → *Secret keys*. Needed server-side from M06. Never commit it or send it to templates.

Database tests are written as **pytest** tests that connect with `DATABASE_URL` and assert constraints / RLS behaviour.
They work the same against cloud or a local stack, and CI can later run them against a staging project.

---

## Milestones

### M03 — Dev environment & configuration ✅ Done 2026-10-10
**Goal:** Anyone can configure the app against the Supabase dev project and check connectivity.
- Cloud dev project created (see table above). No Docker needed.
- `pydantic-settings` config in `app/config.py`: `APP_ENV`, `DATA_BACKEND=mock|supabase`, `SUPABASE_URL`,
  `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY`, `DATABASE_URL` (secrets as `SecretStr`).
- `.env.example` committed, `.env` git-ignored.
- `/healthz` reports app version, backend and Supabase Auth reachability (503 when degraded).
- GitHub Actions workflow runs `pytest` on every push.

**Test gate**
```powershell
copy .env.example .env                     # then fill SUPABASE_URL + SUPABASE_PUBLISHABLE_KEY
.\.venv\Scripts\uvicorn app.main:app --reload
curl http://127.0.0.1:8000/healthz         # {"status":"ok", … "supabase":{"configured":true,"reachable":true,…}}
.\.venv\Scripts\python -m pytest           # 35 passed
```
Details: [milestones/M03-dev-environment-supabase-cloud.md](milestones/M03-dev-environment-supabase-cloud.md).

### M04 — Database schema, constraints & seed data (spec §6, Phase 1) ✅ Done 2026-10-10
**Goal:** The spec §6 schema exists as versioned migrations, loaded with the fictional dataset.
- Migrations in `supabase/migrations/` covering products, BOMs and routings (with operation dependencies),
  sales orders and lines, warehouses, inventory, POs, work centers, machines, shift calendars, holidays,
  downtime, production orders and operations, planning runs, schedule versions, alerts, approvals and audit logs.
- UUID keys, foreign keys, `CHECK` constraints (positive quantities, valid status enums), unique codes, indexes.
- `supabase/seed.sql`: port of `app/data/mock_data.py`, labelled fictional.
- DB-level tests in `tests/db/` (pytest + `DATABASE_URL`).

Details: [milestones/M04-database-schema-and-seed.md](milestones/M04-database-schema-and-seed.md).

**Test gate**
```powershell
npx supabase link --project-ref qemoliyeblfmrccniczr   # once; asks for the DB password
npx supabase db reset --linked   # wipes the DEV project, re-applies all migrations + seed — must succeed
.\.venv\Scripts\python -m pytest tests/db   # unique product_code, qty > 0, status enums, FK integrity
```

### M05 — Authentication, roles & Row Level Security (spec §5) ✅ Done 2026-10-10
**Goal:** Real users with spec roles, enforced in the database.
- Supabase Auth (email + password). `profiles` and `user_roles` tables for Planner, Supervisor, Stores,
  Purchasing, Engineering, Approver, Admin.
- RLS policies per table and role. Approval columns can be changed only by Approver.
- FastAPI login and logout pages. Session cookie holds the user's JWT, which is passed to Supabase on every call.
- Seed one fictional user per role.
- Remove the hard-coded "Sarah Chen" user. Header and permissions come from the session.

**Test gate**
```powershell
.\.venv\Scripts\python -m pytest tests/db/test_rls.py   # planner cannot approve; stores can edit inventory only; …
.\.venv\Scripts\python -m pytest tests/test_auth.py   # login, logout, role-based page access
```
Manual: log in as `planner@example.com`, then as `approver@example.com`. The approval buttons behave differently.
Details: [milestones/M05-auth-roles-rls.md](milestones/M05-auth-roles-rls.md).

### M06 — Supabase repository & master-data editing ✅ Done 2026-10-10
**Goal:** The app runs on the database instead of mock data, and users can edit master data.
- `SupabaseRepository` implementing the same functions as `app/data/repository.py`. Switch via `DATA_BACKEND`.
- Create and edit draft sales orders, with line validation (spec §6.7: never silently change engineering config).
- New screen **Inventory & Purchasing** (spec §12.4): balances, reservations, quality holds, open POs.
- Engineering screen: BOM revision status workflow (Draft → Under Review → Approved).

Details: [milestones/M06-supabase-repository-and-editing.md](milestones/M06-supabase-repository-and-editing.md).

**Test gate**
```powershell
$env:DATA_BACKEND="supabase"; .\.venv\Scripts\python -m pytest   # same route tests pass on the real DB
.\.venv\Scripts\python -m pytest tests/test_crud.py              # create/edit order, inventory update
```

### M07 — Approvals & audit trail (spec §6.21–6.22, §12.8) ✅ Done 2026-10-10
**Goal:** Approvals and consequential changes are persisted and traceable.
- Postgres triggers write `audit_logs` (before/after JSON) for approvals, schedule changes, BOM revisions and
  inventory adjustments.
- Version state transitions (`Draft → Proposed → Approved/Rejected → Superseded`) in a DB function that checks role.
- New screen **Approvals & Audit**: pending plans, decision with comments, audit history.

Details: [milestones/M07-approvals-and-audit.md](milestones/M07-approvals-and-audit.md).

**Test gate**
```powershell
.\.venv\Scripts\python -m pytest tests/db/test_approvals_db.py   # only Approver approves; baseline superseded; audit row
.\.venv\Scripts\python -m pytest tests/test_approvals.py
```

### M08 — Material requirements planning engine (spec §7, Phase 2) ✅ Done 2026-10-10
**Goal:** Correct, time-phased material shortages computed from real data, independent of AI.
- `planning/mrp/`: multilevel explosion (reuse `explode_bom`), net requirements, eligible receipts (open quantity,
  status, QC release, location), time-phased availability, explicit allocation across competing orders.
- `planning_runs` row with input snapshot. Results written to `planning_alerts`.
- Pure functions tested with fixtures. A thin loader reads a DB snapshot in one transaction.

**Test gate**
```powershell
.\.venv\Scripts\python -m pytest planning/tests/mrp   # spec acceptance tests 2, 3, 10 + golden fixtures
.\.venv\Scripts\python -m planning.cli mrp --order SO-1005   # prints requirements & shortages from local DB
```
Details: [milestones/M08-mrp-engine.md](milestones/M08-mrp-engine.md).

### M09 — Finite-capacity scheduler & independent validator (spec §8, Phase 3) ✅ Done 2026-10-10
**Goal:** OR-Tools CP-SAT produces feasible schedules, and a separate validator checks them.
- New screen **Resources & Calendars** (spec §12.5): shifts, holidays, downtime, overtime. These are scheduler inputs.
- `planning/scheduling/`: interval variables, precedence, optional intervals for alternative machines,
  no-overlap, calendars, downtime, material readiness from M08, weighted lateness objective, time limit.
- Solver outcomes exactly as spec §8.7 (Feasible, Feasible with lateness, Needs data, Infeasible, Solver limit).
- `planning/validation/`: independent checks (spec §8.8). A failing schedule cannot be proposed.
- Solver runs in a worker process. The UI polls run status.

**Test gate**
```powershell
.\.venv\Scripts\python -m pytest planning/tests/scheduling   # acceptance tests 1, 4, 5, 6, 7, 8
.\.venv\Scripts\python -m planning.cli schedule --horizon 2025-11-17:2025-12-15
```
Details: [milestones/M09-finite-capacity-scheduler.md](milestones/M09-finite-capacity-scheduler.md). "Re-run CP-SAT" already starts a real run (done in M09).

### M10 — Planning workbench on live schedules & what-if scenarios (spec §10, §12.6, Phase 4)
**Goal:** The Gantt and dashboards show solver output, and planners run scenarios without touching the baseline.
- Gantt reads `schedule_versions` and `production_operations` (done since M06/M09; "Re-run CP-SAT" is real since M09).
- Scenario builder covering supplier delay, machine unavailable, urgent order, quantity change, overtime and priority. Each
  scenario saves as a new version.
- Baseline vs scenario comparison computed from data, replacing the current hard-coded v4.2/v4.3 metrics.

**Test gate**
```powershell
.\.venv\Scripts\python -m pytest tests/test_scenarios.py   # acceptance test 9: baseline preserved, new run flagged
```
Manual: delay PO-8821 by 5 days, run, and compare. The approved baseline is unchanged.

### M11 — AI assistant with tool calling (spec §11, Phase 5)
**Goal:** Replace the scripted assistant with an LLM that only uses authorised tools.
- Tools from spec §11.1 wrap repository and planning functions, run as the logged-in user, and validate inputs.
- System prompt from spec §11.3. No tool can approve, release, place POs or commit dates.
- API key server-side only. Conversation linked to the selected order and version.

**Test gate**
```powershell
.\.venv\Scripts\python -m pytest tests/test_assistant_tools.py   # schemas, authorization, refusals (mocked LLM)
```
Manual (needs API key in `.env`): "Can we deliver SO-1005 by 30 November?" returns tool calls and an answer that matches the data.

### M12 — Hardening & deployment (spec Phase 6)
**Goal:** A controlled MVP ready for supervised use.
- RLS and authorization test sweep, solver performance tests, backups, error monitoring.
- Deploy: separate **production** Supabase project (`supabase db push`), Python app on a container host, secrets management.
- User acceptance run of all 10 spec acceptance tests against the deployed environment.

**Test gate:** full `pytest` (including `tests/db`) green locally against dev, and in CI against a staging project.
