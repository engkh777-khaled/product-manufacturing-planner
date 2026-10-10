# Project Overview

_Last updated: 2026-10-10 (after M02)_

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
| Data | Fictional in-memory dataset behind a repository layer |
| Database / auth (Supabase) | Not started |
| Planning engine (OR-Tools CP-SAT) | Not started — "Re-run CP-SAT" returns the cached run |
| AI assistant | Scripted replies grounded in repository data; no LLM connected |
| Tests | 30 passing (`pytest`) |

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

Target architecture (spec §3): the repository layer will call Supabase (Postgres + RLS) and a separate
Python planning API (FastAPI + OR-Tools). Templates and routes should not need to change.

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
  templating.py         Jinja env, filters (d, money, num), header/sidebar context
  models.py             Pydantic models mirroring spec §6
  planning.py           Deterministic planning helpers (unit-tested)
  assistant.py          Scripted assistant (spec §11.1 tool names)
  data/repository.py    Data-access layer
  data/mock_data.py     Fictional dataset
  routes/               dashboard, workbench, orders, engineering, assistant
  templates/            base.html, pages, components/ (macros), partials/ (HTMX fragments)
  static/css/           input.css (source) → app.css (built, committed)
design/stitch/          Original Stitch designs: code.html + screen.png per screen, DESIGN.md
tests/                  test_planning.py, test_routes.py
development-khaled/     This engineering log
```

## Screens

| Route | Screen | Key behaviour |
|---|---|---|
| `/dashboard` | Executive Dashboard | KPIs computed from data; approval gate refuses planner sign-off (server-side) |
| `/workbench` | Planning Workbench | Custom Gantt; baseline v4.2 ↔ scenario v4.3; critical-path / shortage / PM filters |
| `/orders` | Sales Orders & Demand | Search, status tabs, priority, pagination, order inspector |
| `/engineering` | Engineering & BOM | Multilevel BOM explosion, shortages filter, scrap +5% simulation, validation |
| `/assistant` | AI Planning Assistant | Scripted grounded replies; approval submission needs both confirmations (server-checked) |

## Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
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
