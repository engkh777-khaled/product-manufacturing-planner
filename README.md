# Product Manufacturing Planner — Frontend (MVP)

Python frontend for the **AI Valve Production Planning Agent** ([spec](valve-planning-agent-mvp.md)),
built from the Google Stitch designs in [design/stitch/](design/stitch/) (design system:
[DESIGN.md](design/stitch/precision_industrial_operations/DESIGN.md)).

> **New to the project?** Start with the engineering log in [development-khaled/](development-khaled/) —
> milestones, decisions, and troubleshooting.

> **All data is fictional demo data** (spec §13). Sign-in and roles use Supabase Auth (M05). Set
> `DATA_BACKEND=supabase` in `.env` to run every page on the database (M06); `mock` uses in-memory data.
> No solver or LLM is connected yet.

## Stack
- FastAPI + Jinja2 server-rendered pages, HTMX for partial updates, Alpine.js for small client toggles
- Tailwind CSS v3 via `pytailwindcss` (standalone CLI — no Node required); tokens ported from Stitch into [tailwind.config.js](tailwind.config.js)

## Run
```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env    # then fill in SUPABASE_URL + SUPABASE_PUBLISHABLE_KEY

# Rebuild CSS after changing templates or classes (app.css is committed, so this is optional to just run)
$env:TAILWINDCSS_VERSION = "v3.4.17"
.\.venv\Scripts\tailwindcss -i app/static/css/input.css -o app/static/css/app.css --minify   # add --watch while developing

.\.venv\Scripts\python -m scripts.seed_demo_users   # once: creates the fictional demo users (needs DATABASE_URL)
.\.venv\Scripts\uvicorn app.main:app --reload
```
Open http://127.0.0.1:8000 and sign in, e.g. as `planner@example.com` or `approver@example.com`; the password is
`DEMO_USER_PASSWORD` in your `.env`. Check configuration and Supabase connectivity at http://127.0.0.1:8000/healthz.

## Tests
```powershell
.\.venv\Scripts\python -m pytest
```

## Planning engine (CLI)
```powershell
.\.venv\Scripts\python -m planning.cli mrp                  # material problems from the database
.\.venv\Scripts\python -m planning.cli mrp --item SR-ST6-10  # time-phased availability
.\.venv\Scripts\python -m planning.cli schedule              # CP-SAT schedule + independent validation
.\.venv\Scripts\python -m planning.cli schedule --machine MC-014 --freeze-days 3 --save
.\.venv\Scripts\python -m planning.cli scenario --delay PO-8821:5      # what-if vs today's data
```

## Screens
| Route | Screen | Interactions |
|---|---|---|
| `/dashboard` | Executive Dashboard | Approval gate enforces server-side authorization (planner cannot commit) |
| `/workbench` | Planning Workbench (finite-capacity Gantt) | Version switch, critical-path / shortage / PM filters, real CP-SAT re-run with delivery + validation panels (Supabase mode, planner), baseline-vs-version comparison from data |
| `/scenarios` | What-If Scenarios | Supplier delay, outage, urgent order, quantity, overtime, second machine, priority; compared with today's data, saved as separate versions |
| `/inventory` | Inventory & Purchasing | Stock and PO edits (stores / purchasing), MRP with time-phased availability and Run MRP (planner) |
| `/resources` | Resources & Calendars | Machines, capabilities, shifts, holidays, overtime, downtime; supervisors record downtime and calendar changes |
| `/orders` | Sales Orders & Demand | Search, status tabs, priority filter, pagination, order inspector |
| `/engineering` | Engineering & BOM | Product switch, multilevel BOM expand/collapse, shortages filter, scrap +5% simulation, BOM validation |
| `/approvals` | Approvals & Audit | Approve/reject plans (approver), version lifecycle, append-only audit trail |
| `/assistant` | AI Planning Assistant | With `ANTHROPIC_API_KEY`: tool-calling assistant (11 planning tools, run as you; saves/submissions need your Confirm). Without: scripted, data-grounded replies |

## Layout
```
app/
  main.py, templating.py      FastAPI app, Jinja env + shell context
  models.py                   Pydantic models mirroring spec §6 tables
  planning.py                 Deterministic calcs: net available, yield, BOM explosion, risk, util bands
  assistant.py                Scripted assistant (no LLM) using spec §11.1 tool names
  data/repository.py          Data-access layer — the single swap point for Supabase / planning API
  data/mock_data.py           Fictional dataset
  routes/                     One router per screen
  templates/                  base shell, pages, components/ macros, partials/ HTMX fragments
tests/                        planning unit tests + route tests
```

## Next steps (per spec roadmap)
- Replace `data/repository.py` internals with Supabase queries (RLS, auth) — templates stay unchanged.
- Point "Re-run CP-SAT" at the Python OR-Tools planning service.
- Replace `assistant.respond` with an LLM using tool calling against the same repository functions.
