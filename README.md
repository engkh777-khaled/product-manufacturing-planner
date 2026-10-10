# Product Manufacturing Planner — Frontend (MVP)

Python frontend for the **AI Valve Production Planning Agent** ([spec](valve-planning-agent-mvp.md)),
built from the Google Stitch designs in [design/stitch/](design/stitch/) (design system:
[DESIGN.md](design/stitch/precision_industrial_operations/DESIGN.md)).

> **All data is fictional demo data** (spec §13). Nothing connects to an ERP, Supabase, a solver, or an LLM yet.

## Stack
- FastAPI + Jinja2 server-rendered pages, HTMX for partial updates, Alpine.js for small client toggles
- Tailwind CSS v3 via `pytailwindcss` (standalone CLI — no Node required); tokens ported from Stitch into [tailwind.config.js](tailwind.config.js)

## Run
```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt

# Rebuild CSS after changing templates or classes (app.css is committed, so this is optional to just run)
$env:TAILWINDCSS_VERSION = "v3.4.17"
.\.venv\Scripts\tailwindcss -i app/static/css/input.css -o app/static/css/app.css --minify   # add --watch while developing

.\.venv\Scripts\uvicorn app.main:app --reload
```
Open http://127.0.0.1:8000.

## Tests
```powershell
.\.venv\Scripts\python -m pytest
```

## Screens
| Route | Screen | Interactions |
|---|---|---|
| `/dashboard` | Executive Dashboard | Approval gate enforces server-side authorization (planner cannot commit) |
| `/workbench` | Planning Workbench (finite-capacity Gantt) | Baseline v4.2 / Scenario v4.3 switch, critical-path / shortage / PM filters, solver re-run (demo stub) |
| `/orders` | Sales Orders & Demand | Search, status tabs, priority filter, pagination, order inspector |
| `/engineering` | Engineering & BOM | Product switch, multilevel BOM expand/collapse, shortages filter, scrap +5% simulation, BOM validation |
| `/assistant` | AI Planning Assistant | Scripted, data-grounded replies; human-approval submission gated by both confirmations (server-checked) |

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
