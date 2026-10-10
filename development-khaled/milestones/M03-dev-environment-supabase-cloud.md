# M03 — Dev environment & configuration (Supabase cloud)

| | |
|---|---|
| **Date** | 2026-10-10 |
| **Author** | Khaled (implemented with Claude Code) |
| **Status** | Done |
| **Commits** | `14092f0` |
| **Spec sections** | §3 (stack), §5 security requirements (secrets server-side) |

## Goal
Configure the app against a Supabase project and prove connectivity, without Docker.

## What was done
- **Supabase cloud dev project created** in org *engkh777-khaled's Org*:
  `product-manufacturing-planner-dev`, ref `qemoliyeblfmrccniczr`, region eu-west-3 (Paris), $0/month.
  - The existing project *engkh777-khaled's Project* was **not** used: it hosts another app
    (`products`, `rfq_submissions`, `rfq_items`) and its `products` table would collide with ours.
- [app/config.py](../../app/config.py): `Settings` (pydantic-settings) reading `.env`:
  `APP_ENV`, `DATA_BACKEND`, `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY` (SecretStr),
  `DATABASE_URL` (SecretStr), `HEALTH_TIMEOUT_SECONDS`.
- [app/routes/health.py](../../app/routes/health.py): `GET /healthz`. Calls `{SUPABASE_URL}/auth/v1/health` with
  the publishable key and returns status, version, environment, backend, and Supabase reachability/latency.
  Returns **503** if Supabase is configured but unreachable, or `DATA_BACKEND=supabase` without config.
- [.env.example](../../.env.example) committed. `.env` created locally with URL + publishable key and added to
  [.gitignore](../../.gitignore).
- [.github/workflows/tests.yml](../../.github/workflows/tests.yml): runs `pytest` on push / PR (Python 3.13, mock backend).
- `requirements.txt`: + `pydantic-settings`. App version bumped to `0.2.0`.
- [tests/test_health.py](../../tests/test_health.py): 5 tests (no network; `httpx.get` is monkeypatched,
  `.env` ignored via `Settings(_env_file=None)`), including "secret key never appears in output".

## Decisions
- D-014 Supabase cloud dev project instead of local Docker (supersedes the local-stack part of D-012).

## How to verify (run on 2026-10-10)
```powershell
.\.venv\Scripts\python -m pytest            # 35 passed
.\.venv\Scripts\uvicorn app.main:app --reload
curl http://127.0.0.1:8000/healthz
```
Actual response:
```json
{"status":"ok","version":"0.2.0","environment":"development","data_backend":"mock",
 "supabase":{"configured":true,"reachable":true,"latency_ms":441,"auth_version":"v2.197.0"}}
```

CI: first GitHub Actions run on `14092f0` — **success** ([run](https://github.com/engkh777-khaled/product-manufacturing-planner/actions/runs/38039872895)).

## Known gaps / follow-ups
- **Database password** of the dev project is unknown (project was created via the Supabase API/MCP).
  Reset it in Dashboard → Project Settings → Database before M04 (`npx supabase link`, `DATABASE_URL`).
- **Secret API key** not configured yet. Needed from M06.
- Data still comes from the mock repository (`DATA_BACKEND=mock`). Supabase serves data from M06.

## Problems hit
- Push of `.github/workflows/tests.yml` needs the `workflow` OAuth scope. Fixed with `gh auth refresh -s workflow`.
- `GET /rest/v1/` returns 401 "Secret API key required" with a publishable key, so the health probe uses
  `/auth/v1/health` instead. See [troubleshooting.md](../troubleshooting.md).
