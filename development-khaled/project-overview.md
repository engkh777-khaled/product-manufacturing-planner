# Project Overview

_Last updated: 2026-10-10 (after M10)_

## Goal

An AI-assisted production planning application for a valve manufacturing and assembly factory.
It turns customer sales orders into a material-aware, finite-capacity production schedule, explains
delivery risks, and routes proposed plans to a human approver.

Source of truth for requirements: [valve-planning-agent-mvp.md](../valve-planning-agent-mvp.md).

**Core principle (spec §1): the planning engine calculates; the AI explains, assists, and coordinates.**
The AI must never invent quantities or dates, approve plans, place POs, or release production.

## Current state

| Area | State |
|---|---|
| Frontend (5 screens) | Built — FastAPI + Jinja2 + HTMX/Alpine, matches the Stitch designs |
| Data | `DATA_BACKEND=supabase`: pages read an RLS-filtered DB snapshot; `mock`: in-memory fictional data (M06) |
| Supabase | Cloud dev project: full spec §6 schema (24 tables, RLS default-deny) + fictional seed (M04). Auth/policies next (M05) |
| Configuration | `app/config.py` + `.env` (template `.env.example`) |
| CI | GitHub Actions runs `pytest` on push |
| Planning engine (OR-Tools CP-SAT) | Finite-capacity scheduler + independent validator; "Re-run CP-SAT" runs a background job and saves a Draft version (M09) |
| AI assistant | Scripted replies grounded in repository data; no LLM connected |
| Auth | Supabase Auth sign-in, 7 spec roles, RLS policies on all tables, HTTP-only cookie sessions (M05) |
| Editing | Draft sales orders, inventory, PO lines, BOM revision workflow, approval decisions (Supabase mode) |
| Approvals & audit | Workflow functions + guard (approve ⇒ new baseline), append-only trigger audit on 12 tables (M07) |
| Planning engine | `planning/` package: MRP (multilevel netting, priority allocation, time-phased availability, alerts) + CLI (M08) |
| What-if scenarios | Seven change types, planned next to today's data, compared and saved as separate versions; receipt changes flag the baseline (M10) |
| Tests | 247 passing: 156 app/engine + 91 DB (`tests/db`, need `DATABASE_URL`; skipped in CI) |

## Architecture today

```
Browser ──HTTP──> FastAPI routes (app/routes/*)
                     │  render Jinja2 templates (full pages + HTMX fragments)
                     ▼
              app/data/repository.py   ← single data-access layer (swap point)
                     │
          ┌──────────┴───────────┐
          ▼                      ▼
  app/data/mock_data.py    app/planning.py
  (fictional dataset)      (deterministic calcs: net available, yield,
                            BOM explosion, risk rules, utilization bands)

  app/assistant.py — scripted assistant; reads only via repository + planning
```

Target architecture: Supabase (Postgres + Auth + RLS) behind the repository layer, plus a Python planning package
(MRP + OR-Tools CP-SAT) run by a worker. Templates and routes should not need to change.
The milestone plan is in [backend-roadmap.md](backend-roadmap.md).

## Stack

| Layer | Choice | Why — see [decisions.md](decisions.md) |
|---|---|---|
| Web framework | FastAPI | Same stack as the future planning API (D-002) |
| Templates | Jinja2, server-rendered | Stitch designs are already HTML + Tailwind (D-002) |
| Interactivity | HTMX 2.0.4 + Alpine.js 3.14.8 (CDN) | Partial updates without a JS build (D-003) |
| Styling | Tailwind CSS v3.4.17 via `pytailwindcss` | No Node.js needed (D-004) |
| Models | Pydantic v2 | Mirrors spec §6 tables (D-005) |
| Tests | pytest + FastAPI TestClient | |
| Python | 3.13 | |

## Folder map

```
app/
  main.py               FastAPI app, static mount, routers, "/" → /dashboard
  config.py             Settings from env/.env (Supabase URL/keys, DATA_BACKEND, AUTH_MODE)
  auth.py               Sign-in, token refresh, JWKS verification, session cookies, require_user
  templating.py         Jinja env, filters (d, money, num), header/sidebar context
  models.py             Pydantic models mirroring spec §6
  planning.py           Deterministic planning helpers (unit-tested)
  assistant.py          Scripted assistant (spec §11.1 tool names)
  data/repository.py    Page logic over the request's dataset (mock or Supabase)
  data/dataset.py       Per-request dataset + user token (context variables)
  data/supabase_dataset.py  planning_snapshot() → models; 30 s cache per token
  data/writes.py        Writes via PostgREST as the user (RLS enforced)
  data/mock_data.py     Fictional dataset
  routes/               health (/healthz), auth (/login, /logout), dashboard, workbench, orders, engineering, assistant
  templates/            base.html, pages, components/ (macros), partials/ (HTMX fragments)
  static/css/           input.css (source) → app.css (built, committed)
design/stitch/          Original Stitch designs: code.html + screen.png per screen, DESIGN.md
tests/                  test_planning.py, test_routes.py, test_health.py; db/ = database tests
pytest.ini              Short tracebacks (never print secrets)
supabase/               config.toml, migrations/ (schema + RLS), seed.sql (fictional data)
scripts/                seed_demo_users.py — fictional role users (password only in .env)
planning/               Pure planning engine: mrp/ (M08), scheduling/ (CP-SAT, M09), validation/, scenarios.py (M10), cli.py, tests/
.github/workflows/      tests.yml — CI
.env.example           Config template (.env itself is git-ignored)
development-khaled/     This engineering log
```

## Screens

| Route | Screen | Key behaviour |
|---|---|---|
| `/dashboard` | Executive Dashboard | KPIs computed from data; approval gate refuses planner sign-off (server-side) |
| `/workbench` | Planning Workbench | Custom Gantt per version; real CP-SAT runs (M09) with delivery + validation panels; data-driven comparison (M10) |
| `/scenarios` | What-If Scenarios | Builder, background runs, saved scenarios, comparison pages (M10) |
| `/orders` | Sales Orders & Demand | Search, status tabs, priority, pagination, order inspector |
| `/inventory` | Inventory & Purchasing | Balances, QC holds, PO lines; stores/purchasing edit inline (M06); MRP (M08) |
| `/resources` | Resources & Calendars | Machines, capabilities, shifts, holidays/overtime, downtime; supervisors edit (M09) |
| `/engineering` | Engineering & BOM | Multilevel BOM, revision history + approval workflow, scrap simulation, validation |
| `/approvals` | Approvals & Audit | Pending plans with impact vs baseline, decisions, version lifecycle, audit trail (M07) |
| `/assistant` | AI Planning Assistant | Scripted grounded replies; approval submission needs both confirmations (server-checked) |

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env                               # fill in Supabase URL + publishable key + DATABASE_URL
.\.venv\Scripts\python -m scripts.seed_demo_users     # demo users; password saved to .env
.\.venv\Scripts\uvicorn app.main:app --reload        # http://127.0.0.1:8000
.\.venv\Scripts\python -m pytest                     # tests
```

Rebuild CSS after changing templates or Tailwind classes:

```powershell
$env:TAILWINDCSS_VERSION = "v3.4.17"
.\.venv\Scripts\tailwindcss -i app/static/css/input.css -o app/static/css/app.css --minify
```

## Repositories

| Remote | URL | Use |
|---|---|---|
| `planner` | https://github.com/engkh777-khaled/product-manufacturing-planner (private) | **This project.** Local `main` tracks `planner/main` |
| `origin` | https://github.com/engkh777-khaled/AI-AGENT | Older repo; untouched since `b7e2f07` |

## Supabase

| Project | Ref | Region | Use |
|---|---|---|---|
| `product-manufacturing-planner-dev` | `qemoliyeblfmrccniczr` | eu-west-3 | Shared, disposable dev database |
| _(engkh777-khaled's Project)_ | `nydvjpygmamsejpkxfts` | eu-west-3 | **Another app — do not use** |

