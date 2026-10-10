# M12 — Hardening (deployment pending)

| | |
|---|---|
| **Date** | 2026-10-10 |
| **Author** | Khaled (implemented with Claude Code) |
| **Status** | Hardening done; production deployment waiting for decisions (host, production project) |
| **Commits** | _see git log — "M12" commit_ |
| **Spec sections** | Phase 6 (data validation, authorization and RLS testing, audit, solver performance, backup and recovery, UAT, documentation), §5 security requirements |

## Goal
A controlled MVP ready for supervised use: proven permissions, trustworthy validation, operational safety, and a
deployable artifact — with the deployment itself waiting for the owner's decisions.

## What was done
**Security fixes found by the sweep**
- **Signed validation** (migration `20261010163956_signed_validation`, D-036): `record_schedule_run(p_text,
  p_signature)` only accepts `validation_passed` when the payload is HMAC-signed by the app server
  (`app/signing.py`; key in `.env` and `private.app_config`, set by `scripts/set_signing_key.py`). A forged or
  unsigned claim is stored as *not validated* with a note. Closes the D-029 residual risk.
- **Decided records are locked** (`20261010193117_lock_decided_records`, D-037): the matrix showed approvers could
  edit approved BOM/routing headers and the approved baseline (their broad RLS rights exist for the approval
  functions). Triggers now allow approvers only Under Review → Approved and Approved → Superseded; approved,
  superseded and obsolete revisions are locked for everyone; decided schedule versions are read-only outside the
  approval workflow; approvers cannot edit versions at all. An approver can no longer approve a Draft BOM without
  engineering review.
- **Run timing** (`20261010192202_clamp_run_timing`): `started_at` from the app clock is clamped to the
  database's `completed_at` (fixes intermittent save failures when clocks differ).

**Authorization and RLS sweep** — [tests/db/test_rls_matrix.py](../../tests/db/test_rls_matrix.py): update rights of
all 7 roles on 25 table/row cases, RLS on every table, anonymous and role-less users read nothing, 7 tamper cases.

**Solver performance** — [planning/tests/scheduling/test_performance.py](../../planning/tests/scheduling/test_performance.py):
48 lines / ~260 operations / 12 machines with downtime and material gates: solved and validated in ≈ 11 s; a
0.05 s limit still returns the proven-feasible greedy schedule, validated.

**Operations** — [app/hardening.py](../../app/hardening.py): security headers (CSP restricted to the actual CDNs,
frame/sniff/referrer/permissions policies, HSTS in production), request ids + access log, friendly 500 page with
a reference (no tracebacks), rate limits (login 10/min per IP+email, assistant 20/min per user), production
configuration refused unless complete. `/livez` liveness next to `/healthz` readiness.

**Data validation** — [planning/data_quality.py](../../planning/data_quality.py): engineering gaps, unqualified or
uncalendared operations, undated/overdue receipts, impossible balances, demand errors, inactive machines in the
baseline; `python -m planning.cli check-data [--strict]` and a panel on Resources & Calendars. Dev data: 2 errors
(SO-1021 needs data), 1 warning, 2 notes (sub-assemblies without lead time).

**Backup and recovery** — [scripts/export_data.py](../../scripts/export_data.py): logical export of all 26 tables
with manifest and checksums, `--verify` against the live schema; dev export: 587 rows, verified.

**Deployment artifacts** — `Dockerfile` (non-root, no secrets, health check, single worker), `.dockerignore`, CI
job building and smoke-testing the image; CI actions moved to Node 24 versions.

**Documentation** — [operations.md](../operations.md) (environments, secrets, container, monitoring, migrations,
backups, go-live checklist) and [acceptance-tests.md](../acceptance-tests.md) (spec tests 1–10 → evidence).

**Tests** — 327 passing (198 app/engine + 129 DB); new: RLS matrix and tamper (33), performance (3), hardening (6), data
quality (8), backup (2), signed validation (1).

Supabase security advisor after the migrations: only *Leaked Password Protection Disabled* (dashboard item) and
an INFO note that `private.app_config` has RLS without policies — intentional: no client may read the key.

## Decisions
- D-036 Validation results are signed by the server.
- D-037 Approved and decided records are locked by triggers, not only RLS.
- D-038 Single-process deployment until a shared job queue exists.

## How to verify
```powershell
.\.venv\Scripts\python -m pytest                                 # full suite incl. tests/db
.\.venv\Scripts\python -m planning.cli check-data
.\.venv\Scripts\python -m scripts.export_data; .\.venv\Scripts\python -m scripts.export_data --verify backups\<file>
```
CI: jobs `pytest` and `container` green.

## Waiting for decisions (not done)
- **Production Supabase project** (creation may cost money; region) → `supabase db push`, production signing key,
  Auth settings (sign-ups off, leaked-password protection, admin MFA).
- **Container host** (e.g. Fly.io, Render, Azure Container Apps, Cloud Run) and secret store.
- **Error tracking service** (e.g. Sentry) — optional.
- **Staging project for CI DB tests** — the roadmap's CI gate; needs a second project.
- **User acceptance run** of spec tests 1–10 on production (matrix ready).
- Shared job queue / state before running more than one app process.
