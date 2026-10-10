# Decision Log

Architectural and process decisions, newest last. Never edit a past decision's outcome — add a new
entry with `Supersedes: D-NNN` instead.

Format: **Context** (why a choice was needed) · **Decision** · **Consequences** (trade-offs, follow-ups).

---

## D-001 — Build the frontend first, on mock data
_2026-10-10 · M01_

**Context.** The spec's roadmap (§15, §19) starts with database and planning engine, but a full set of
UI designs (Google Stitch) already existed and stakeholders needed something to see and react to.

**Decision.** Build all five designed screens now against a typed, fictional in-memory dataset, behind
a single data-access module (`app/data/repository.py`).

**Consequences.** Spec Phase 1–3 (schema, MRP, scheduler) are still outstanding. Every page reads
through the repository, so wiring Supabase / the planning API later should not touch templates.
Numbers shown in the UI are demo data and must be labelled as such.

## D-002 — Python (FastAPI + Jinja2) instead of the spec's Next.js
_2026-10-10 · M01_

**Context.** Spec §3 recommends Next.js/TypeScript for the frontend. The project owner asked to use
Python. The planning engine is Python/FastAPI per spec anyway.

**Decision.** Server-rendered FastAPI + Jinja2. Alternatives considered: Reflex (pure Python, but every
Stitch screen would need rewriting as components) and NiceGUI/Streamlit/Dash (own styling, hard to
match the dense design).

**Consequences.** One language across UI and planning service. Stitch HTML could be ported almost
directly. Rich client-side interactions (drag-to-reschedule on the Gantt) will need more JS later.

## D-003 — HTMX for partial updates, Alpine.js for local UI state
_2026-10-10 · M01_

**Decision.** HTMX handles server round-trips (order table filtering, inspector, BOM simulation,
validation, chat, approval actions). Alpine handles purely client-side toggles (Gantt filters, BOM
expand/collapse, checkbox-gated submit button). Both loaded from unpkg, no build step.

**Consequences.** Fragment endpoints live next to page routes (`/orders/table`,
`/orders/{id}/inspector`, `/engineering/{code}/bom`, …). Server must still validate anything the
UI gates (see D-007).

## D-004 — Tailwind v3 via `pytailwindcss`, built CSS committed
_2026-10-10 · M01_

**Context.** Stitch output uses a Tailwind config with custom tokens. No Node.js on the dev machine.

**Decision.** Use the standalone Tailwind CLI through `pytailwindcss`, pinned to `v3.4.17`
(`TAILWINDCSS_VERSION`). Port the Stitch tokens into `tailwind.config.js` and add the semantic colors
from DESIGN.md (`warn`, `crit`, `ok`, `ai`). Commit `app/static/css/app.css` so the app runs without a build.

**Consequences.** Rebuild CSS after changing classes. `tailwind.config.js` scans `app/**/*.py` too,
because some classes (Gantt order colors) live in Python data.

## D-005 — Domain models mirror spec §6; only UI-needed fields
_2026-10-10 · M01_

**Decision.** `app/models.py` defines Pydantic models named after spec §6 tables (Product, BomHeader,
BomItem, SalesOrder, PurchaseOrder, WorkCenter, Machine, ScheduleVersion, ApprovalRequest, AuditEntry …),
trimmed to the fields the screens use.

**Consequences.** When writing Supabase migrations, start from spec §6, not from these models; then
extend the models to match.

## D-006 — Deterministic calculations live in `app/planning.py`, not templates
_2026-10-10 · M01_

**Decision.** Every computed figure in the UI (net available, required quantity with yield, shortage,
lateness, risk level, utilization band, BOM explosion, cycle detection) comes from pure functions in
`app/planning.py`, covered by unit tests.

Conventions fixed here:
- **Yield** = 1 − scrap; required = parent qty × qty-per-parent ÷ yield (scrap applied once, spec §7.1).
- Shortage = ceil(required) − net available (discrete parts).
- **Utilization bands:** normal < 85 %, constrained 85–92 %, bottleneck > 92 %.
- **Risk** (spec §9): Unknown if data incomplete; High if late, critical shortage, or capacity conflict;
  Medium if margin < 2 days; else Low. Rule-based, never presented as a probability.

## D-007 — Human approval is enforced on the server
_2026-10-10 · M01_

**Context.** Spec §2, §5, §11.3: the AI must never approve plans; planners cannot approve their own.

**Decision.** `repository.request_decision` refuses approve/reject unless the current user has
`can_approve` and logs the attempt. `/assistant/submit` re-checks both planner confirmations server-side
and only records a submission — the version stays `Proposed`. The scripted assistant refuses
approve/release/commit requests.

**Consequences.** Tests cover all three (`test_planner_cannot_commit_plan`,
`test_submit_requires_both_confirmations_server_side`, `test_submit_never_auto_approves`). Keep these
guarantees when real auth arrives.

## D-008 — Reconcile inconsistent design data into one story
_2026-10-10 · M01_

**Context.** The Stitch screens disagree with each other (e.g. SO-1005 due Nov 24 on one screen,
Nov 30 on another; SO-1012 is a different customer on different screens).

**Decision.** One consistent fictional scenario: SO-1005 (Shell Offshore GOM) requested Nov 30, baseline
finishes Dec 3 because actuator PO-8821 arrives Nov 28 and Hydro Rig 1 (WC-40) is the bottleneck;
scenario v4.3 (expedite + Saturday OT) finishes Nov 29. SO-1021 demonstrates "needs data" (draft BOM,
no routing). SO-1022 demonstrates competing allocation for PO-8821.

## D-009 — Semantic badge colors from DESIGN.md, not Stitch's error-container
_2026-10-10 · M01_

**Context.** Several Stitch badges used `bg-error-container` with `text-on-error` — dark red on dark red, unreadable.

**Decision.** Badges follow DESIGN.md "Micro-Status Badges": 10 % tinted fill, 30 % border, full-color
text (`crit`, `warn`, `ok`, `info`, `ai`, `muted`) via the `badge` macro in
`app/templates/components/ui.html`.

## D-010 — New dedicated GitHub repo, private, separate remote
_2026-10-10 · M02_

**Decision.** Code lives in `engkh777-khaled/product-manufacturing-planner` (private), added as remote
`planner`. The pre-existing `origin` (`AI-AGENT`) is left untouched. The design zip is not committed
(its contents are in `design/stitch/`).

## D-011 — Backend stays Python; database is Supabase
_2026-10-10 · roadmap_

**Context.** The project owner chose Supabase for the database and Python for the backend, and asked whether
another backend language would suit the project better.

**Decision.** Keep Python. OR-Tools CP-SAT (spec §8.5) has its most complete API in Python, and the rest of
the stack is already Python (FastAPI, pandas, `supabase-py`, AI SDKs). Performance is not a reason to switch,
because the solver runs in native C++ under every language binding.

**Consequences.** Solver runs are CPU-bound, so they run in a worker process, never inside a web request (M09).
Rich browser interactions (Gantt drag) will be added as small JS components, not a language change.
Full plan: [backend-roadmap.md](backend-roadmap.md).

## D-012 — Every milestone must be testable locally (Supabase CLI + Docker)
_2026-10-10 · roadmap_

**Decision.** Local Supabase stack via `npx supabase start` (needs Docker Desktop). Schema changes go only
through migrations in `supabase/migrations/`. `supabase db reset` must rebuild everything from scratch.
DB tests use pgTAP (`supabase test db`). Each milestone in the roadmap defines a local test gate.

**Consequences.** Docker Desktop must be installed (not present on the dev machine as of 2026-10-10).
Fallback: a separate free Supabase cloud project for development.

## D-013 — App calls Supabase as the logged-in user; repository keeps one interface
_2026-10-10 · roadmap (Proposed — confirm in M05/M06)_

**Decision.** The FastAPI app passes the user's Supabase JWT on every data call so RLS is enforced. The
service-role key is used only for trusted server jobs (planning snapshots), with explicit authorization checks.
`DATA_BACKEND=mock|supabase` selects the repository implementation so the existing route tests run against both.

**Consequences.** Planning runs read a consistent snapshot in a single transaction (direct Postgres
connection), separate from user-scoped CRUD calls.

## D-014 — Supabase cloud dev project instead of local Docker (for now)
_2026-10-10 · M03 · Supersedes: the local-stack part of D-012_

**Context.** Docker Desktop is not installed on the dev machine, and the project owner chose Supabase cloud for now.

**Decision.** Use a dedicated free cloud project `product-manufacturing-planner-dev` (ref `qemoliyeblfmrccniczr`,
eu-west-3) as the shared development database. Migrations still live in `supabase/migrations/` and are applied
with the Supabase CLI (`npx supabase link` + `db push` / `db reset --linked`). Database tests are pytest tests
using `DATABASE_URL` instead of pgTAP, so they run against cloud or local unchanged. The owner's existing project
is not reused (it hosts another app with a conflicting `products` table).

**Consequences.** The dev database is shared and disposable; never store real data in it. Production will be a
separate project (M12). A local Docker stack can be added later without changing migrations or tests. The D-012
rule "every milestone has a test gate runnable from a developer machine" still applies.

## D-015 — Schema conventions
_2026-10-10 · M04_

**Decision.**
- **Components are products** (`item_type` = finished_good | manufactured | purchased). A multilevel BOM is a chain
  of single-level BOMs: each manufactured component with a structure has its own `bom_headers` row. This is the
  standard MRP model and lets the same component appear in many parents.
- **Status/priority values are `text` + `CHECK`**, not Postgres enum types, so adding a value is a simple migration.
- **Business rules live in the database** (constraints, partial unique indexes, triggers), so they hold for the
  UI, scripts, the solver and the AI assistant alike.
- **Production orders and operations are stored per schedule version.** Scenarios copy, never mutate, the baseline.
- **RLS enabled on every table from day one, default-deny** (no policies) until M05 defines role policies.
- Plant times are stored as `timestamptz`; the demo plant is CST (UTC-06).

**Consequences.** The UI mock and the seed differ in a few operation numbers and one product (see M04 known gaps);
the seed is authoritative from M06.

## D-016 — Applying migrations through the Supabase API, aligned with CLI history
_2026-10-10 · M04_

**Context.** The dev database password was not available, so `npx supabase db push` could not run.

**Decision.** Migrations were applied with the Supabase API (MCP `apply_migration`) and the local files were renamed
to the exact versions recorded remotely (`supabase_migrations.schema_migrations`). The seed was run once via SQL.
From now on either path works: `npx supabase migration new` → `npx supabase db push` (with password), or the API —
but **always keep file names equal to the remote versions**.

**Consequences.** `npx supabase db reset --linked` rebuilds the dev DB entirely from the repo files.

## D-017 — Session design: HTTP-only cookies, local token verification, identity via RLS
_2026-10-10 · M05_

**Decision.** The browser never holds Supabase tokens in JavaScript: the FastAPI app signs in with the password grant
and stores access + refresh tokens in HTTP-only, SameSite=Lax cookies (Secure outside dev/test). Each request verifies
the access token locally with the project's JWKS (ES256, cached 10 min, re-fetched on unknown `kid`) and refreshes it
when expired. Identity and roles come from `public.me()` called with the user's token, so they pass through RLS.

**Consequences.** No extra Auth round trip per request; data calls in M06 will forward the same user token to
PostgREST so RLS applies. Auth outages keep the session (user is asked again next request) instead of logging everyone out.

## D-018 — SECURITY DEFINER helpers live in a private, non-exposed schema
_2026-10-10 · M05_

**Context.** RLS policies need `has_role()`-style helpers that read `user_roles` without recursion (SECURITY DEFINER).
In `public` they were also callable via `/rest/v1/rpc/…` (advisor lint 0029).

**Decision.** Helpers live in schema `private` (not exposed by PostgREST); `authenticated` keeps EXECUTE because
policies run as the caller. The only API function, `public.me()`, is SECURITY INVOKER.

## D-019 — Demo users via script; no credentials in the repository
_2026-10-10 · M05_

**Decision.** `scripts/seed_demo_users.py` creates the 7 fictional role users through `DATABASE_URL`. Their shared
password is generated into the developer's `.env` (`DEMO_USER_PASSWORD`) and only a bcrypt hash is stored in Postgres.
`seed.sql` contains no users. Re-run the script after `supabase db reset --linked`.

## D-020 — One RLS-filtered snapshot per request, converted to the existing models
_2026-10-10 · M06_

**Context.** Pages, repository logic and tests were built on in-memory models. The database has a different
(normalised) shape, and spec §4 requires planning views to use a consistent snapshot.

**Decision.** `public.planning_snapshot()` (SECURITY INVOKER, one statement) returns everything the caller may see.
`app/data/supabase_dataset.py` converts it into the same models as `mock_data`; the request middleware installs it
in a context variable that the repository reads via `ds()`. Cached 30 s per token, invalidated after writes.
If Supabase fails the request gets 503 — never a silent fallback to demo data.

**Consequences.** Both backends share all page logic and tests (offline tests use a recorded snapshot). The whole
plant is loaded per request; acceptable for the MVP data size — revisit with targeted RPCs for real volumes.

## D-021 — Multi-row writes are SECURITY INVOKER database functions
_2026-10-10 · M06_

**Decision.** Writes that touch several rows (save a draft order with its lines, confirm an order, approve a BOM
revision and supersede the previous one) are Postgres functions called via `/rest/v1/rpc`, running with the caller's
RLS. Single-row edits use PostgREST PATCH with the user's token. An RLS-hidden row makes PATCH update nothing, which
the app reports as "no permission".

**Consequences.** Atomic changes without a service-role key in the app; the database re-validates every rule.

## D-022 — Capacity loads are planning-run outputs stored on the schedule version
_2026-10-10 · M06_

**Decision.** Work-center load and machine utilisation live in `schedule_versions.metrics`
(`work_center_load`, `machine_utilization`), like other run KPIs. Until the solver exists they are labelled seed
values (`load_source`); M09 writes computed values into the same keys.

## D-023 — Approval transitions only through database functions, enforced by a guard trigger
_2026-10-10 · M07_

**Context.** Spec §6.19 / §12.8: an approved plan becomes the baseline, the previous baseline is preserved, and nobody
approves their own plan. With plain table updates an approved request and the baseline could drift apart.

**Decision.** `submit_plan_for_approval`, `decide_plan_approval`, `withdraw_plan_approval` (SECURITY INVOKER, RLS
applies) are the only way to move plans through the workflow. A BEFORE trigger on `schedule_versions` and
`approval_requests` rejects approve / reject / supersede / baseline changes unless the transaction-local flag set by
`decide_plan_approval` is on — for every role, including the service role and the owner (maintenance mode excepted).

**Consequences.** "Request approved" always implies "baseline switched, old baseline Superseded". The M09 solver must
insert new versions as Draft and submit them through the same function.

## D-024 — Audit by triggers, append-only, owner-only maintenance mode
_2026-10-10 · M07_

**Decision.** A SECURITY INVOKER row trigger on 12 business tables writes `audit_logs` with full before/after JSON,
the acting user and database role, a business label, and the transaction's `app.audit_reason` (set by workflow
functions from user comments). `audit_logs` rejects UPDATE/DELETE/TRUNCATE for everyone. The database owner can switch
a session into maintenance mode (`app.audit_maintenance = on`) to seed or clean up test data without audit noise; API
callers cannot set it. The app only writes *app events* (blocked attempts, solver requests).

**Consequences.** Every change is audited no matter which client made it (app, scripts, SQL editor, future solver).
Audit volume grows with writes; add retention/archiving before production (M12).

## D-025 — Level-by-level MRP with priority-then-need-date allocation
_2026-10-10 · M08_

**Context.** Spec §7.1 asks to consolidate shared component demand across orders while preserving pegging;
acceptance test 10 requires that the same stock is never allocated twice and that the allocation is explainable.

**Decision.** Explode one BOM level for all open order lines, allocate every requirement of that level across orders
— order priority first, then need date, requested date, order/line — taking the earliest-available remaining supply,
then explode only unmet sub-assembly quantities to the next level. Requirements stay pegged to their order line;
each alert names the higher-priority orders that received the stock.

**Consequences.** Deterministic and explainable; quantities are fractional (expected scrap) with discrete shortages
rounded up. The rule is fixed in code until the business agrees configurable allocation rules.

## D-026 — Need dates from the baseline schedule; MRP owns the material alert types
_2026-10-10 · M08_

**Decision.** A component is needed at the start of its consuming operation in the approved baseline
(`bom_items.operation_reference`), else at the production-order start (never before as-of). `record_mrp_run()` stores
each run and replaces all open `material_shortage`, `late_supplier_receipt`, `quality_hold`, `missing_bom` and
`missing_routing` alerts; schedule-owned alerts (capacity, delivery risk) are untouched.

**Consequences.** Open material alerts always describe the latest MRP run. MRP is infinite-capacity; M09 closes the
loop by making material-ready dates hard constraints in the scheduler.
