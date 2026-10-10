# M01 — Frontend from Stitch designs

| | |
|---|---|
| **Date** | 2026-10-10 |
| **Author** | Khaled (implemented with Claude Code) |
| **Status** | Done |
| **Commits** | `afb6ce1` |
| **Spec sections** | §12 (UI), §6 (models), §7.1 (BOM explosion), §9 (risk), §11 (assistant), §5 (approval rules) |

## Goal
A working frontend reproducing the five Stitch screens, in Python, on fictional data, structured so
the real database and planning engine can be plugged in later.

## What was done

**Setup**
- Python 3.13 virtualenv, [requirements.txt](../../requirements.txt) (FastAPI, Uvicorn, Jinja2, Pydantic,
  python-multipart, pytailwindcss, pytest, httpx).
- [tailwind.config.js](../../tailwind.config.js): Stitch tokens + DESIGN.md semantic colors;
  [app/static/css/input.css](../../app/static/css/input.css): tabular numbers, Gantt hatch patterns, filter classes.

**Core modules**
- [app/models.py](../../app/models.py) — Pydantic models mirroring spec §6.
- [app/planning.py](../../app/planning.py) — `net_available`, `yield_factor`, `required_qty`, `util_pct`,
  `util_band`, `lateness_days`, `risk_level`, `explode_bom`, `find_cycles`, `validate_bom`.
- [app/data/mock_data.py](../../app/data/mock_data.py) — fictional dataset: 3 products, 3 BOMs (one Draft),
  2 routings, 7 work centers / 14 machines, 10 sales orders, 4 POs, 2 schedule versions (v4.2 baseline,
  v4.3 scenario), 1 approval request.
- [app/data/repository.py](../../app/data/repository.py) — all reads/writes: order views & filters,
  KPIs, capacity views, shortages, alerts, BOM view/validation, Gantt geometry, approvals, audit log.
- [app/assistant.py](../../app/assistant.py) — scripted assistant: order questions, bottleneck,
  version comparison, at-risk list, refusal of approvals.

**Screens** ([app/routes/](../../app/routes/), [app/templates/](../../app/templates/))
- Shared shell [base.html](../../app/templates/base.html): header, sidebar with live telemetry, mobile drawer.
- Macros [components/ui.html](../../app/templates/components/ui.html): `icon`, `button`, `badge`,
  `kpi_card`, `panel_header`, `util_bar`.
- `/dashboard`, `/workbench`, `/orders`, `/engineering`, `/assistant` plus HTMX fragment endpoints.

**Tests** — [tests/test_planning.py](../../tests/test_planning.py) (calculation rules) and
[tests/test_routes.py](../../tests/test_routes.py) (pages render, filters, 404, scrap simulation,
validation, assistant grounding, approval guardrails).

## Decisions
- D-001 Build frontend first on mock data · D-002 FastAPI + Jinja2 instead of Next.js ·
  D-003 HTMX + Alpine · D-004 Tailwind via pytailwindcss, CSS committed · D-005 Models mirror spec §6 ·
  D-006 Calculations in `planning.py` · D-007 Server-enforced human approval ·
  D-008 One consistent demo story · D-009 Readable semantic badges.
  Details in [decisions.md](../decisions.md).

## How to verify
```powershell
.\.venv\Scripts\python -m pytest          # result on 2026-10-10: 30 passed
.\.venv\Scripts\uvicorn app.main:app --reload
```
Manual checks done: each route compared to its `design/stitch/*/screen.png` via headless Chrome
screenshots at 1600 px; HTMX and Alpine confirmed loaded in a real browser (Alpine-disabled submit
button, HTMX indicator styles injected); no horizontal overflow at 375 px on `/dashboard`.

## Known gaps / follow-ups
- No database, no authentication, single hard-coded user (Sarah Chen, planner, cannot approve).
- Writes (submissions, audit entries) are in process memory and reset on restart.
- "Re-run CP-SAT", "Export …", "Create Customer Sales Order" are stubs or disabled.
- Assistant is scripted keyword matching, not an LLM.
- Gantt is read-only (no drag-to-reschedule); horizon fixed to Nov 17 – Dec 5, 2025 (demo dates).
- Screens from spec §12 without designs are not built: Inventory & Purchasing, Resources & Calendars,
  Approvals & Audit.

## Problems hit
- Missing Tailwind token made Gantt text invisible → added token.
- Shortage KPI used 1-unit build instead of order quantity → fixed in `repository.shortage_skus`.
- BOM validation used 1-unit build → `validate_bom` now takes `build_qty`.
- Cycle check could not detect cycles in a parent-pointer tree → added `find_cycles`.
- See [troubleshooting.md](../troubleshooting.md).
