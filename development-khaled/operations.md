# Operations runbook

_Last updated: 2026-10-10 (M12). Audience: whoever deploys and runs the planner._

## Environments
| Environment | Supabase project | App | Status |
|---|---|---|---|
| Development | `product-manufacturing-planner-dev` (`qemoliyeblfmrccniczr`, eu-west-3) | local `uvicorn` | in use; fictional data |
| Production | **not created yet** (decision pending: cost, region) | container host **not chosen yet** | — |

Never point development tools at another project; the account also hosts an unrelated project.

## Configuration and secrets
All configuration is environment variables (template: `.env.example`). Production refuses to start unless
`DATA_BACKEND=supabase`, Supabase URL/publishable key and `SCHEDULE_SIGNING_KEY` are set; `AUTH_MODE=disabled`
is impossible outside development/test.

| Secret | Where it lives | Notes |
|---|---|---|
| `SUPABASE_PUBLISHABLE_KEY` | host env | safe for browsers; RLS applies |
| `SCHEDULE_SIGNING_KEY` | host env **and** `private.app_config` | set both with `python -m scripts.set_signing_key`; rotate with `--rotate` (existing validated drafts keep their flag; new runs use the new key) |
| `ANTHROPIC_API_KEY` | host env | optional; without it the assistant is scripted |
| `DATABASE_URL` | operator machine only | owner connection for CLI, exports, tests — never on the app host unless needed |
| `SUPABASE_SECRET_KEY` | not used by the app | never deploy it to the app or the browser |

## Container
```bash
docker build -t pmp .
docker run -p 8000:8000 --env-file prod.env pmp      # prod.env from the secret store, never committed
```
- Non-root (uid 10001), no `.env` inside the image (`.dockerignore`), `HEALTHCHECK` on `/livez`.
- **One worker per container and one container** for now: solver jobs, assistant conversations, pending
  confirmations and rate limits are in process memory. Scaling out needs a shared job queue/state (follow-up).
- Behind the platform's TLS proxy (`--proxy-headers`): HSTS is sent in production; cookies are `Secure`.
- CI builds the image and smoke-tests it on every push (`.github/workflows/tests.yml`, job `container`).

## Health and monitoring
| Endpoint | Use |
|---|---|
| `/livez` | liveness (process answers; no external calls) — container health check |
| `/healthz` | readiness — Supabase Auth reachable, latency, configuration; 503 when degraded |

- Logs go to stdout: one access line per request (`method path status ms user rid`), errors with the same
  request id (`X-Request-ID` header; users see it on the error page). `LOG_LEVEL` sets verbosity.
- Assistant tool calls and confirmed actions are logged (`app.agent.tools`); consequential actions are also in
  the audit log.
- Supabase: Database → Advisors (security/performance) after every migration; Auth → rate limits.
- Error tracking service (e.g. Sentry) not wired yet — decision pending; the request-id logs are the hook.

## Database changes
- Migrations live in `supabase/migrations/`, named with the remote version timestamp. Dev: applied through the
  Supabase MCP / dashboard. New or production project without the CLI: `python -m scripts.setup_database --no-seed`
  (empty database) or `--upgrade` (apply pending migrations); with the CLI: `supabase link` + `supabase db push`.
  `--check` compares the repository's migrations with the database history.
- After applying: `get_advisors` / Advisors page, then `python -m pytest tests/db` against dev.
- Seed (`supabase/seed.sql`) is fictional demo data — **never** run it on production.

## Backups and recovery
1. **Supabase backups** (project dashboard → Database → Backups): check the plan's retention before go-live;
   enable point-in-time recovery for production if the plan allows.
2. **Logical export** (business data, all public tables, with manifest and checksums):
   ```powershell
   .\.venv\Scripts\python -m scripts.export_data                              # → backups/pmp-<UTC>.json
   .\.venv\Scripts\python -m scripts.export_data --verify backups\pmp-<UTC>.json
   ```
   Auth users and passwords are not exported. `backups/` is git-ignored; store copies encrypted.
3. **Recovery check** (monthly and before go-live): verify the latest export (checksums + table coverage vs the
   live schema), restore a Supabase backup into a scratch project, run `python -m pytest tests/db` against it.

## Data quality
`python -m planning.cli check-data` (or the Resources & Calendars page) lists data that would make plans wrong:
missing engineering data, unqualified or uncalendared operations, undated/overdue receipts, impossible balances.
`--strict` exits 1 on errors (cron / CI against a staging copy).

## Security checklist before go-live
- [ ] Supabase Auth: public sign-ups **off**, leaked-password protection **on**, MFA for admins
- [ ] GitHub (public repository, D-040): secret scanning and push protection **on**
- [ ] Production `SCHEDULE_SIGNING_KEY` generated and stored in both places; dev key not reused
- [ ] Demo users not created in production (`scripts/seed_demo_users.py` is dev-only)
- [ ] Advisors clean; `tests/db` green against a staging copy
- [ ] Backups verified (above); restore rehearsed once
