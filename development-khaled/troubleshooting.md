# Troubleshooting

Problems hit during development and how they were solved. Add new entries at the bottom.

---

## `git push` / `git ls-remote` hangs with no output
_Seen 2026-10-10 (M02)_

**Symptom.** Push to `planner` hangs indefinitely; `GIT_CURL_VERBOSE=1` shows the schannel TLS
handshake stuck at `step 2/3 … need more data`. `gh api` calls work fine.

**Cause.** Unstable network path to github.com interacting badly with Git's default Windows TLS
backend (schannel). Credentials were fine (`gh auth git-credential get` returns instantly).

**Fix.** Use OpenSSL and HTTP/1.1 for the push:
```powershell
git -c http.sslbackend=openssl -c http.version=HTTP/1.1 push
```
To make it permanent for this repo: `git config http.sslbackend openssl` and `git config http.version HTTP/1.1`.

## `gh auth login` via `! gh auth login` in the IDE chat did nothing
_Seen 2026-10-10 (M02)_

**Fix.** Run the device flow non-interactively and complete it in the browser:
```bash
echo | gh auth login --hostname github.com --git-protocol https --web
```
It prints a one-time code; enter it at https://github.com/login/device.

## `pip install` times out
_Seen 2026-10-10 (M01)_

**Fix.** Retry with a longer timeout: `pip install --timeout 60 -r requirements.txt`.

## A Tailwind color class has no effect
_Seen 2026-10-10 (M01)_

**Symptom.** Gantt bars for SO-1020 had invisible text.

**Cause.** `text-on-tertiary-fixed` was used but the token was missing from `tailwind.config.js`,
so Tailwind silently generated nothing.

**Fix.** Add the token, rebuild CSS. To check whether a class exists in the build:
```bash
grep -o 'text-on-tertiary-fixed{[^}]*}' app/static/css/app.css
```

## Headless Chrome screenshots look clipped at phone width
_Seen 2026-10-10 (M01)_

**Cause.** `chrome --headless --window-size=390,…` lays the page out wider than 390 px, then crops.
It is not a real overflow.

**How to really check overflow.** Load the page in a 375 px same-origin iframe and compare
`document.documentElement.scrollWidth` to `clientWidth` (both were 375 for `/dashboard`).

## Git commits show author "Copilot"
_Seen 2026-10-10 (M02)_

**Cause.** This working copy's Git identity is set to "Copilot". Set your own before committing:
```powershell
git config user.name "Your Name"
git config user.email "you@example.com"
```

## `GET /rest/v1/` returns 401 "Secret API key required"
_Seen 2026-10-10 (M03)_

**Cause.** With the new Supabase API keys, the PostgREST root (OpenAPI schema) only accepts a **secret** key.
Normal table queries with the publishable key still work once tables and RLS policies exist.

**Fix.** For connectivity checks use the Auth health endpoint, which accepts the publishable key:
```bash
curl -H "apikey: $SUPABASE_PUBLISHABLE_KEY" "$SUPABASE_URL/auth/v1/health"
```

## Don't know the Supabase database password
_Seen 2026-10-10 (M03)_

**Cause.** The dev project was created through the Supabase API, which does not return the password.

**Fix.** Dashboard → Project Settings → Database → *Reset database password*. Store it only in `.env`
(`DATABASE_URL`) and enter it when `npx supabase link` asks.

## Push rejected: "refusing to allow an OAuth App to create or update workflow"
_Seen 2026-10-10 (M03)_

**Cause.** The GitHub CLI token had scopes `gist, read:org, repo` but not `workflow`, which is required to push
files under `.github/workflows/`.

**Fix.**
```bash
echo | gh auth refresh --hostname github.com --scopes workflow   # approve the code at github.com/login/device
gh auth status                                                   # scopes now include 'workflow'
```

## Connecting Python / pytest to the Supabase database (`DATABASE_URL`)
_Seen 2026-10-10 (M04)_

**Symptom.** Connection to `db.<ref>.supabase.co:5432` times out from some networks.

**Cause.** The direct database host on the free plan is IPv6-only.

**Fix.** Use the **Session pooler** string: Dashboard → **Connect** → *Session pooler*. It looks like
`postgresql://postgres.qemoliyeblfmrccniczr:<password>@aws-<n>-eu-west-3.pooler.supabase.com:5432/postgres`.
Put it in `.env` as `DATABASE_URL=...` (never commit it), then `pytest tests/db`.

## `supabase db push` wants to re-apply migrations that already exist
_Prevented 2026-10-10 (M04)_

**Cause.** Local migration file timestamps differ from the versions recorded in the remote
`supabase_migrations.schema_migrations` table (e.g. after applying SQL through the dashboard or the API).

**Fix.** Rename local files to the remote versions (`npx supabase migration list` shows both columns), or repair the
history with `npx supabase migration repair --status applied <version>`.

## `DATABASE_URL` password problems
_Seen 2026-10-10 (M04)_

- The line must start with `DATABASE_URL=` (a bare `postgresql://…` line is ignored).
- Remove the `[ ]` from the dashboard's `[YOUR-PASSWORD]` placeholder.
- Special characters must be **percent-encoded**: `@`→`%40`, `#`→`%23`, `%`→`%25`, `/`→`%2F`, `:`→`%3A`.
  Easiest: use the dashboard's *Generate a password* (letters and digits only).
- `FATAL: password authentication failed` = wrong password, not a network problem. **Stop retrying**: repeated failures
  make Supabase temporarily ban your IP. Reset the password, or unban under Database settings → *Network Bans*.

## Test output must never show secrets
_Seen 2026-10-10 (M04)_

**Cause.** pytest's default (long) tracebacks print function arguments — psycopg's included the decoded connection
string with the password.

**Fix (in place).** `pytest.ini` sets `--tb=short`; `tests/db/conftest.py` catches connection errors and reports only
the server's first message line. Don't add `-l`/`--showlocals` or `--tb=long` when DB credentials are configured.

## Dates off by a day when converting `timestamptz`
_Seen 2026-10-10 (M04)_

**Cause.** In Postgres, `AT TIME ZONE '-06'` is a POSIX-style offset whose sign is **inverted** (it means UTC+6).

**Fix.** Use a named zone: `planned_finish AT TIME ZONE 'America/Chicago'` (the demo plant's zone).

## Advisor: "Signed-In Users Can Execute SECURITY DEFINER Function" (lint 0029)
_Seen 2026-10-10 (M05)_

**Fix.** Move the function to a schema PostgREST does not expose (`private`) with `ALTER FUNCTION … SET SCHEMA private`.
Policies keep working (they reference functions by OID). Keep `GRANT EXECUTE … TO authenticated` if policies call it.

## psycopg: "cannot insert multiple commands into a prepared statement" / "could not determine data type of parameter"
_Seen 2026-10-10 (M05)_

- Run one statement per `execute()` when passing parameters.
- Inside functions like `jsonb_build_object`, cast parameters explicitly: `%(email)s::text`.

## Sign-in works in the app but a page keeps redirecting to /login
_Reference (M05)_

Check, in order: cookies `pmp_access` / `pmp_refresh` exist (browser dev tools); `/healthz` shows Supabase reachable;
the user has at least one row in `public.user_roles` (users without a role are treated as signed out because
`me()` returns no roles and RLS hides all data); re-run `python -m scripts.seed_demo_users` after a DB reset.
