# M05 — Authentication, roles & Row Level Security

| | |
|---|---|
| **Date** | 2026-10-10 |
| **Author** | Khaled (implemented with Claude Code) |
| **Status** | Done |
| **Commits** | `8579b2e` |
| **Spec sections** | §5 (roles, permissions, security requirements), §11.3 rule 10 (AI never approves) |

## Goal
Real users with the spec §5 roles; permissions enforced by the database (RLS) and by the app, never only by the UI.

## What was done

**Database** ([supabase/migrations/](../../supabase/migrations/))
- `20261010101820_auth_roles_and_rls`: `profiles`, `user_roles` (planner, supervisor, stores, purchasing, engineering,
  approver, admin), trigger creating a profile for every new auth user, role helpers, `public.me()`, and
  **select / insert / update / delete policies for all 26 tables**.
- `20261010101854_move_role_helpers_to_private_schema`: security-advisor fix (see D-018).

**Permission model (RLS)**

| Data | Read | Write |
|---|---|---|
| All plant data | any user **with a role** | — |
| Work centers, machines, shifts, warehouses | | admin |
| Calendar exceptions, machine downtime | | supervisor, admin |
| Products | | engineering, admin |
| BOM / routing revisions | | engineering, admin: **Draft / Under Review only** · approver: approve/supersede |
| BOM lines, routing operations, dependencies | | engineering, admin — only while the revision is Draft / Under Review |
| Inventory balances | | stores, admin |
| Purchase orders / lines | | purchasing, admin (stores may update lines: receipts) |
| Sales orders / lines | | planner, admin (delete only Draft) |
| Planning runs, production orders/operations | | planner, admin (supervisor updates progress) |
| Schedule versions | | planner: Draft/Proposed only, never baseline · approver: decides, **not on versions they created** |
| Approval requests | | planner submits own · approver decides **others'** requests only |
| Audit logs | approver, admin | insert own entries only; no update/delete for anyone |
| User roles | own roles (admin: all) | admin |

**App** ([app/auth.py](../../app/auth.py), [app/routes/auth.py](../../app/routes/auth.py), [app/main.py](../../app/main.py))
- `/login` (design-system page, demo accounts listed only when `APP_ENV=development`), `/logout` (POST).
- Supabase password sign-in; access + refresh tokens in **HTTP-only, SameSite=Lax** cookies (`Secure` outside dev/test).
- Middleware verifies the access token **locally against the project JWKS (ES256)**, refreshes it when expired,
  clears cookies for forged/invalid tokens, keeps them if the Auth service is temporarily unreachable.
- Identity + roles from `public.me()` called *as the user* (cached 60 s per token).
- Every page router requires a signed-in user. Full-page requests redirect to `/login?next=…`; HTMX requests get
  `401` + `HX-Redirect`. `next` is restricted to local paths (no open redirect).
- Header shows the real user, job title, roles and a sign-out menu. The hard-coded "Sarah Chen" is gone
  (kept only as the `AUTH_MODE=disabled` demo user for tests).
- App-side checks mirror RLS: only approvers decide plans, never their own submission; only planners/admins submit.
- `AUTH_MODE=disabled` is rejected by config validation unless `APP_ENV` is development/test.

**Demo users** — [scripts/seed_demo_users.py](../../scripts/seed_demo_users.py) creates 7 fictional users
(`planner@`, `supervisor@`, `stores@`, `purchasing@`, `engineering@`, `approver@`, `admin@example.com`), assigns roles,
and links seed records (BOM approvals → engineering, APR-1042 requester → planner, approver → approver…).
Password: generated once into `.env` as `DEMO_USER_PASSWORD`; only the bcrypt hash reaches the database.

**Tests**
- [tests/test_auth.py](../../tests/test_auth.py) — 14 tests with a mocked Supabase Auth and a real ES256 key:
  redirects, HTMX 401, wrong password, HTTP-only cookies, header identity, open-redirect block, token refresh,
  forged token rejected, sign-out, planner can't commit, approver can, no self-approval, only planners submit,
  `AUTH_MODE=disabled` refused in production.
- [tests/db/test_rls.py](../../tests/db/test_rls.py) — 17 tests acting as each demo user inside rolled-back transactions.
- [tests/conftest.py](../../tests/conftest.py) — app tests run with `AUTH_MODE=disabled`.

## Decisions
- D-017 Session design: server-side cookies + local JWT verification + identity via RLS-scoped RPC.
- D-018 SECURITY DEFINER role helpers live in a non-exposed `private` schema.
- D-019 Demo users created by script; no passwords in the repository.

## How to verify (run 2026-10-10)
```powershell
.\.venv\Scripts\python -m scripts.seed_demo_users   # 7 demo users ready
.\.venv\Scripts\python -m pytest                    # 103 passed (49 app + 54 DB)
.\.venv\Scripts\uvicorn app.main:app --reload       # open /login, sign in as planner@ and approver@example.com
```
Live check against the dev project: unauthenticated `/dashboard` → 303 `/login`; planner sign-in → header
"Sarah Chen", reject APR-1042 refused and logged; approver sign-in → header "Marcus Vance", reject allowed;
sign-out clears cookies; wrong password → 401 generic message.
Supabase advisors after M05: **security: 0 findings**; performance: only "unused index" (INFO).
Real sign-in also confirmed directly against Supabase: staff see 10 sales orders, anonymous requests see 0.

## Known gaps / follow-ups
- **Public sign-ups are still enabled** in Supabase Auth settings. Users who sign themselves up get no role and
  therefore see nothing, but turn it off: Dashboard → Authentication → Sign In / Providers → *Allow new users to sign up*.
- Pages still render the in-memory mock data (`DATA_BACKEND=mock`); RLS-scoped reads arrive with M06.
- No password-reset / invite flow yet (admins create users via script or dashboard).
- In-memory approval decisions in mock mode reset on restart (persisted in M07).

## Problems hit
- Security advisor WARN (lint 0029): SECURITY DEFINER helpers callable via `/rest/v1/rpc` → moved to `private` schema.
- psycopg can't run multiple statements with parameters in one `execute` → split statements in the seed script.
- `could not determine data type of parameter` in `jsonb_build_object(… %s …)` → explicit `::text` casts.
- Self-approval of an approval request raises an RLS error (requester can see own request) rather than updating 0 rows
  — desired behaviour, test adjusted.
