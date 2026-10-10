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

## Page shows "Plant data unavailable" (503)
_Reference (M06)_

With `DATA_BACKEND=supabase` the app refuses to show demo data when the snapshot fails. Check `/healthz`, that you are
signed in, and that the user has a role (no role → RLS returns no rows). Switch back with `DATA_BACKEND=mock`.

## `ZoneInfoNotFoundError: 'America/Chicago'` on Windows
_Seen 2026-10-10 (M06)_

Windows has no IANA time-zone database. `pip install tzdata` (in requirements.txt).

## A component shows as short by 1 unit although stock equals the order quantity
_Seen 2026-10-10 (M06)_

Expected: required = qty ÷ (1 − scrap) is rounded **up** to whole parts (e.g. 10 valves with 2 % scrap need 11
bodies). Increase stock or check the scrap factor; it is not a bug.

## PL/pgSQL: `record "new" has no field "…"` in a trigger shared by several tables
_Seen 2026-10-10 (M07)_

**Cause.** PL/pgSQL resolves `NEW.column` when the expression runs, even if an earlier `AND` condition is false.
A trigger function attached to two tables cannot mention a column that only one of them has in the same expression.

**Fix.** Branch first (`IF tg_table_name = '…' THEN … ELSIF …`), and also branch by `tg_op` before touching `OLD`.

## A SECURITY INVOKER function says "not found" although the row exists
_Seen 2026-10-10 (M07)_

**Cause.** `SELECT … FOR UPDATE` applies the table's UPDATE policies too; rows the caller may read but not update vanish.

**Fix.** Use a plain `SELECT` for checks (and let the guarded UPDATE + unique indexes handle races), or check
visibility separately to give a precise error.

## "Audit log entries are append-only" when cleaning up test data
_Reference (M07)_

Expected. As the database owner, switch the session to maintenance mode first:
```sql
select set_config('app.audit_maintenance', 'on', false);   -- session; use true inside a transaction
```
Maintenance mode also skips auditing and opens the approval workflow guard. **Dev projects only.**

## "Approving, rejecting or superseding a schedule must go through decide_plan_approval()"
_Reference (M07)_

Expected for direct UPDATEs. Use `select public.decide_plan_approval('APR-…', 'Approved', 'comment')` as an approver
(the app does this), or maintenance mode for data repair on a dev project.

## `UnicodeEncodeError: 'charmap' codec can't encode character '\u2192'` in a script
_Seen 2026-10-10 (M07)_

The Windows console uses cp1252. Run with `PYTHONIOENCODING=utf-8` (or `python -X utf8`).

## MRP gives stock to a later need of the same order
_Seen 2026-10-10 (M08)_

**Cause.** Allocating order-by-order (line 1, then line 2) ignores that component need dates differ per operation.

**Fix (in place).** Level-by-level allocation across all orders, sorted by priority, then **component** need date.
Check any surprising allocation with `python -m planning.cli mrp --item <code>` (time-phased view).

## "Short 3" although only 2.63 units are missing
_Reference (M08)_

Requirements include expected scrap and are fractional; shortages of discrete (EA) parts are reported rounded up.

## MRP says "Needs data"
_Reference (M08)_

At least one open order line has no approved BOM or routing (e.g. SO-1021 / V-800-FGV-02). Its demand cannot be
planned; the rest of the plan is still computed. Approve the engineering data (Engineering & BOM page).

## CP-SAT finds no solution / takes very long
_Seen 2026-10-10 (M09)_

**Cause.** An `AddElement` over every time slot (thousands of values) is fully encoded by CP-SAT (80 000 variables).

**Fix (in place).** Piecewise start formulation, greedy hint, per-line search window, `cp_model_probing_level = 0`.
Debug with `CPSAT_LOG=1 python -m planning.cli schedule` (prints the solver log).

## Signed out at random right after logging in
_Seen 2026-10-10 (M09)_

**Cause.** This PC's clock was a few seconds behind Supabase; a fresh access token's `iat` looked like the future
(`ImmatureSignatureError`), so the session was treated as invalid and the cookies were cleared.

**Fix (in place).** `verify_access_token` allows 30 s leeway (`CLOCK_SKEW_SECONDS`). Still keep Windows time sync on.

## "Version … fails the database schedule re-check" on submit or approval
_Reference (M09)_

Stored operations overlap, break precedence, hit confirmed downtime or sit on an unqualified machine — usually
because downtime was confirmed or operations were edited after the run. Re-run the solver and submit the new draft.

## "Only record_schedule_run() can mark a schedule as validated"
_Reference (M09)_

`validation_passed` cannot be set by hand (D-029). Run the solver; its validation decides.

## Seed-count DB tests fail after using the app
_Seen 2026-10-10 (M09)_

App runs add alerts, versions and operations to the dev DB. The seed tests now count only seeded runs (< #1100) and
versions 4.2/4.3. `supabase db reset --linked` restores the pure seed.

## Scenario comparison shows lines that "moved" for no reason
_Seen 2026-10-10 (M10)_

**Cause.** Two independent time-limited CP-SAT solves can place untouched operations differently.

**Fix (in place).** The scenario solve is anchored to the reference plan (D-032); changes under 1 h are ignored.

## "Version … is a what-if scenario … cannot be submitted or approved"
_Reference (M10)_

By design (D-031). Apply the change to real data (e.g. move the PO date, add the order) and re-run the solver;
submit that version instead.

## A PO date moved but no baseline alert appeared
_Reference (M10)_

Only moves to a *later* date (or removing the date) are flagged, and only when an approved-baseline operation that
consumes the item — directly or through a sub-assembly — starts before the new date minus sub-assembly lead time.
