# Product Manufacturing Planner (MVP)

Production planning for a **fictional valve factory**, built to the spec in
[valve-planning-agent-mvp.md](valve-planning-agent-mvp.md): sales orders, BOMs and routings, inventory and
purchasing, multilevel **MRP**, **finite-capacity scheduling with OR-Tools CP-SAT** checked by an
**independent validator**, **what-if scenarios**, a human **approval workflow** with an append-only audit log, and
an **AI planning assistant** that can only act through authorised tools. Python (FastAPI) + Supabase
(Postgres, Auth, Row Level Security). UI built from the Google Stitch designs in [design/stitch/](design/stitch/).

> **Reviewing this project?** Start with **[REVIEWING.md](REVIEWING.md)** — what to read, how to run it, what to check.
>
> **All data is fictional demo data** (spec §13). The app is not deployed yet
> ([operations.md](development-khaled/operations.md)).

## Quick start — demo mode (no accounts or keys needed)
Python 3.13 (on Windows, clone into a short folder such as `C:\dev` — see [REVIEWING.md](REVIEWING.md)):
```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
"APP_ENV=development", "AUTH_MODE=disabled", "DATA_BACKEND=mock" | Set-Content .env
.\.venv\Scripts\uvicorn app.main:app --reload        # open http://127.0.0.1:8000
```
Every screen runs on built-in fictional data, signed in as a demo planner. The solver, MRP, scenarios and the AI
assistant work on the database: see [REVIEWING.md §3](REVIEWING.md#3-run-everything-with-a-database) for the full
setup (Supabase project, `python -m scripts.setup_database`, demo users, signing key, optional AI key).

## Tests
```powershell
.\.venv\Scripts\python -m pytest          # app + planning engine; tests/db also run when DATABASE_URL is set
```
CI (GitHub Actions) runs the tests and builds and smoke-tests the container image on every push.

## Planning engine (CLI, needs DATABASE_URL)
```powershell
.\.venv\Scripts\python -m planning.cli mrp                        # material problems
.\.venv\Scripts\python -m planning.cli schedule                   # CP-SAT schedule + independent validation
.\.venv\Scripts\python -m planning.cli scenario --delay PO-8821:5  # what-if vs today's data (read-only)
.\.venv\Scripts\python -m planning.cli check-data                 # plant-data quality report
```

## Screens
| Route | Screen | What it does |
|---|---|---|
| `/dashboard` | Executive Dashboard | KPIs, delivery risk, shortages, capacity, approval gate (server-side authorization) |
| `/workbench` | Planning Workbench | Gantt per schedule version, real CP-SAT re-run (planner), delivery + validation panels, baseline comparison |
| `/scenarios` | What-If Scenarios | Supplier delay, outage, urgent order, quantity, overtime, second machine, priority — compared with today's data |
| `/orders` | Sales Orders & Demand | Search, filters, order inspector, draft order editing (planner) |
| `/inventory` | Inventory & Purchasing | Stock and PO edits (stores / purchasing), MRP with time-phased availability |
| `/resources` | Resources & Calendars | Machines, capabilities, shifts, holidays, overtime, downtime (supervisor), data-quality findings |
| `/engineering` | Engineering & BOM | Multilevel BOM, revision workflow (engineering → approver), scrap simulation |
| `/approvals` | Approvals & Audit | Submit / approve / reject plans (never your own), version history, append-only audit trail |
| `/assistant` | AI Planning Assistant | With `ANTHROPIC_API_KEY`: AI with 11 planning tools; saving or submitting needs your Confirm. Without: scripted demo |

## Project layout
```
app/                FastAPI app — routes/, templates/, data/ (datasets, repository, Supabase writes),
                    agent/ (AI assistant: tools + loop), jobs.py (background solver), hardening.py, signing.py
planning/           planning engine, pure Python — mrp/, scheduling/ (CP-SAT), validation/, scenarios.py,
                    data_quality.py, cli.py, tests/
supabase/           migrations/ (schema, RLS policies, workflow functions, guards), seed.sql (fictional data)
scripts/            setup_database, seed_demo_users, set_signing_key, export_data, check_assistant
tests/              app tests; tests/db = database tests (need DATABASE_URL)
design/stitch/      the original UI designs
development-khaled/ engineering log: milestones M00–M12, decisions, troubleshooting, operations, acceptance tests
```

## Documentation
- [development-khaled/README.md](development-khaled/README.md) — milestone timeline (one commit per milestone)
- [project-overview.md](development-khaled/project-overview.md) · [decisions.md](development-khaled/decisions.md) ·
  [troubleshooting.md](development-khaled/troubleshooting.md)
- [operations.md](development-khaled/operations.md) — configuration, secrets, container, monitoring, backups
- [acceptance-tests.md](development-khaled/acceptance-tests.md) — spec acceptance tests 1–10 → evidence

Rebuilding CSS (only after changing templates or classes; `app/static/css/app.css` is committed):
`$env:TAILWINDCSS_VERSION = "v3.4.17"; .\.venv\Scripts\tailwindcss -i app/static/css/input.css -o app/static/css/app.css --minify`
