# Reviewing the Product Manufacturing Planner

A guide for anyone reviewing this project: what to read, how to run it, and what to check. Everything in the
repository is **fictional demo data**; no real company, person or order is involved.

**Status:** all 12 milestones are built (M00–M12). The app is not deployed yet (production project and host
pending). Tests: 200+ app/engine tests and 130 database tests; CI runs on every push.

## 1. Read it (no setup)
| Start with | Why |
|---|---|
| [README.md](README.md) | what the system does, screens, layout |
| [valve-planning-agent-mvp.md](valve-planning-agent-mvp.md) | the specification everything is built against |
| [development-khaled/README.md](development-khaled/README.md) | milestone timeline — **each milestone is one commit**, so the history can be reviewed step by step |
| [project-overview.md](development-khaled/project-overview.md) | current state, stack, layout |
| [decisions.md](development-khaled/decisions.md) | why things are built this way (D-001…D-039) |
| [acceptance-tests.md](development-khaled/acceptance-tests.md) | spec acceptance tests 1–10 → the tests and live checks that prove them |
| [operations.md](development-khaled/operations.md) | configuration, secrets, container, backups, go-live checklist |

Key code:
| Area | Where |
|---|---|
| MRP (multilevel netting, allocation, alerts) | [planning/mrp/](planning/mrp/) |
| Finite-capacity scheduler (CP-SAT) and explanations | [planning/scheduling/](planning/scheduling/) |
| Independent schedule validator | [planning/validation/validator.py](planning/validation/validator.py) |
| What-if scenarios | [planning/scenarios.py](planning/scenarios.py) |
| Database: schema, RLS, workflow functions, guards | [supabase/migrations/](supabase/migrations/) |
| AI assistant: tools (its only access) and loop | [app/agent/tools.py](app/agent/tools.py), [app/agent/loop.py](app/agent/loop.py) |
| Web app (routes, templates, data access) | [app/](app/) |

## 2. Run it in demo mode (about 10 minutes, nothing to sign up for)
Needs **Python 3.13** and git.

> **Windows:** clone into a short folder such as `C:\dev` (at most ~70 characters). Some packages have very long
> file names, and without Windows long-path support `pip install` fails with *No such file or directory …
> anthropic\types\beta\…* in deeper folders.

Windows (PowerShell):
```powershell
git clone https://github.com/engkh777-khaled/product-manufacturing-planner.git
cd product-manufacturing-planner
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
"APP_ENV=development", "AUTH_MODE=disabled", "DATA_BACKEND=mock" | Set-Content .env
.\.venv\Scripts\uvicorn app.main:app --reload
```
macOS / Linux:
```bash
git clone https://github.com/engkh777-khaled/product-manufacturing-planner.git
cd product-manufacturing-planner
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
printf 'APP_ENV=development\nAUTH_MODE=disabled\nDATA_BACKEND=mock\n' > .env
.venv/bin/uvicorn app.main:app --reload
```
Open http://127.0.0.1:8000. You are signed in as a fictional planner and every screen runs on built-in demo data.
The solver, MRP, scenarios, approvals against the database and the AI assistant need §3.

With Docker instead: `docker build -t pmp .` then
`docker run --rm -p 8000:8000 -e APP_ENV=development -e AUTH_MODE=disabled -e DATA_BACKEND=mock pmp`.

Run the tests: `.\.venv\Scripts\python -m pytest` (or `.venv/bin/python -m pytest`). Without `DATABASE_URL` the
database tests are skipped.

> Verified 2026-10-11 on Windows from a fresh clone of the public repository: install 47 s, all 10 pages load,
> 201 tests pass (129 database tests skipped).

## 3. Run everything with a database
### 3a. Use the owner's dev project (quickest)
Ask the owner to invite you to the Supabase organisation and to send the `.env` values **through a password manager
or another secure channel — never by e-mail, chat or git**:
`SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `DEMO_USER_PASSWORD` (to sign in), `SCHEDULE_SIGNING_KEY` (to save
validated plans) and, only if you run the CLI or database tests, `DATABASE_URL`. Set `DATA_BACKEND=supabase` and
`APP_ENV=development`. For the AI assistant use **your own** `ANTHROPIC_API_KEY`.

### 3b. Use your own free Supabase project (independent, nothing shared)
1. Create a project at https://supabase.com (free plan is enough; note the database password you choose).
2. In the project: **Connect** → copy the *Session pooler* connection string; **Project Settings → API** → copy the
   URL and the publishable key. Create `.env`:
   ```
   APP_ENV=development
   DATA_BACKEND=supabase
   SUPABASE_URL=https://<your-ref>.supabase.co
   SUPABASE_PUBLISHABLE_KEY=sb_publishable_...
   DATABASE_URL=postgresql://postgres.<your-ref>:<password>@aws-...pooler.supabase.com:5432/postgres
   ```
3. Set up the database and the demo users (one transaction; it refuses to touch a database that is already set up):
   ```powershell
   .\.venv\Scripts\python -m scripts.setup_database --check   # optional: shows what will happen
   .\.venv\Scripts\python -m scripts.setup_database           # all migrations + fictional demo data
   .\.venv\Scripts\python -m scripts.seed_demo_users          # 7 demo users; password written to .env
   .\.venv\Scripts\python -m scripts.set_signing_key          # key for signed validation, in .env and the DB
   ```
4. Optional AI assistant: add `ANTHROPIC_API_KEY=` and check it with `python -m scripts.check_assistant`.
5. Start the app (as in §2) and sign in as `planner@example.com`, `approver@example.com`, … (password:
   `DEMO_USER_PASSWORD` in `.env`). `python -m pytest` now also runs the database tests against your project.

> Verified so far: the setup script's check mode against the dev database (all 20 migrations match its history)
> and its behaviour with unit tests. A full run on a brand-new project has not been recorded yet — if a step fails,
> please open an issue with the output.

Demo users (all fictional): `planner`, `supervisor`, `stores`, `purchasing`, `engineering`, `approver`, `admin`
`@example.com` — one per spec §5 role.

## 4. What to check
- **Planning correctness:** MRP never allocates the same stock twice; the scheduler respects calendars, downtime,
  precedence, material readiness and frozen work; a late date is reported with its limiting factors, never
  fabricated (spec §14 tests 1–10, mapped in [acceptance-tests.md](development-khaled/acceptance-tests.md)).
- **Safety rules:** no one approves their own plan; an unvalidated or what-if schedule can never be approved;
  approved records are locked; the audit log is append-only ([decisions.md](development-khaled/decisions.md)
  D-023, D-029, D-031, D-036, D-037).
- **Security:** RLS on every table, a role × table permission matrix ([tests/db/test_rls_matrix.py](tests/db/test_rls_matrix.py)),
  secrets only in `.env` / the host, security headers and rate limits ([app/hardening.py](app/hardening.py)).
- **AI boundaries:** the assistant has no tool that approves, releases production, places purchase orders or
  commits dates; saving and submitting need a human Confirm ([app/agent/tools.py](app/agent/tools.py)).

## 5. Known limitations
- Not deployed; one app process only (background jobs, assistant conversations and rate limits are in memory).
- Overtime has no cost model; MRP and the scheduler run in one pass (no re-allocation of stock after scheduling).
- The dashboard's "AI Planning Copilot" cards are static demo text, not AI output.
- More in each milestone's *Known gaps* section.

## 6. Feedback
Open a GitHub issue, or comment on a commit (each milestone is one commit) — please include the screen or file and
the steps to reproduce.
