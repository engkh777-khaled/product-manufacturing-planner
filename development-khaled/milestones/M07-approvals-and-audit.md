# M07 — Approvals & audit trail

| | |
|---|---|
| **Date** | 2026-10-10 |
| **Author** | Khaled (implemented with Claude Code) |
| **Status** | Done |
| **Commits** | `038c51f` |
| **Spec sections** | §5 (Approver; no self-approval; AI never approves), §6.19 (versions, baseline), §6.21–6.22, §8.8, §10, §12.8, acceptance test 9 |

## Goal
Approving a plan really changes the baseline schedule; every consequential change is recorded in an audit trail
that nobody can edit; approvers have a screen to review plans, see their impact and decide.

## What was done

**Workflow — database functions** (SECURITY INVOKER, RLS still applies) in `20261010110418_approvals_and_audit`
- `submit_plan_for_approval(version, comments)` — planner/admin only; version must be Draft/Proposed and have passed
  validation; one pending request per version; creates `APR-2000…` (sequence), version → Proposed.
- `decide_plan_approval(request, Approved|Rejected, comments)` — approver only; never on their own request or on a
  version they created; approval needs validation. **Approve:** previous baseline → *Superseded* (kept, spec
  acceptance test 9), this version → *Approved* + baseline. **Reject:** version → *Rejected*, baseline untouched.
- `withdraw_plan_approval(request)` — requester only; version back to Draft.
- **Workflow guard** (triggers on `schedule_versions`, `approval_requests`): approving, rejecting, superseding or
  changing the baseline is impossible *outside* `decide_plan_approval()` — also for the service role and the database
  owner (fixed in `20261010111225_fix_workflow_guard`). So "request approved" always means "baseline switched".

**Audit trail**
- Row-change trigger on 12 tables (orders + lines, approval requests, schedule versions, BOM headers/lines, routing
  headers/operations, inventory balances, PO lines, user roles, machine downtime) writes `audit_logs` with
  who (`user_id` + `actor_role`), what (`entity_type`, `entity_label` such as *SO-1005*, *GS-PK-010 @ WH-MAIN*,
  *supervisor@example.com · stores*), action (`insert`/`update`/`delete` or `status: Draft → Proposed`), full
  `before_data` / `after_data`, and `reason` (the submit/decision comments). No-op updates are not logged.
- **Append-only:** UPDATE / DELETE / TRUNCATE on `audit_logs` raise an error — for everyone, including the owner.
- **Maintenance mode** (`set_config('app.audit_maintenance','on')`, effective only for the owner role
  `postgres`): seed scripts and test cleanup run without audit rows and may remove test audit rows. API callers
  cannot reach it (PostgREST does not expose `set_config`).
- App events that never touch a table (blocked approval attempts, solver run requests) are written by the app;
  data changes are no longer double-logged by the app (`record_audit(..., app_event=True)` only for app events).
- `audit_logs.actor_role` defaults to the inserting role (`20261010111707_audit_actor_role_default`).

**Screen — Approvals & Audit** (`/approvals`, new nav item; [app/routes/approvals.py](../../app/routes/approvals.py))
- Pending plans: request, version, requester, submitter comments, validation badge, **plan vs baseline** (late orders,
  lateness, cost, violations) and **schedule changes** (every order whose completion date moves, with Δ days and
  late/on-time colouring), decision form with comments (approver), *Withdraw* (requester), explanations otherwise.
- Schedule version lifecycle and decision history.
- Audit trail with entity filter and row limit, changed fields as `field: old → new` (user ids shown as names).
  Visible to approvers/admins only (RLS); others see an explanation.
- Workbench *Submit for Approval* now submits the version on screen; dashboard approval buttons and the assistant's
  submission use the same workflow.

**Tests**
- [tests/db/test_approvals_db.py](../../tests/db/test_approvals_db.py) — 15 SQL-level tests as real demo users
  (rolled back): submit rules, decide rules, baseline switch, rejection, self-approval and own-version blocks,
  withdraw, guard against direct updates (even as owner), append-only audit, before/after values, no-op updates,
  role-grant auditing.
- [tests/test_approvals.py](../../tests/test_approvals.py) — 8 offline tests (page on demo data and on the recorded
  snapshot, schedule changes, decisions, submit/withdraw, diff formatting).
- `tests/db/test_writes.py` — API round trip submit → duplicate refused → planner refused → approver rejects;
  inventory write proven audited; the module removes its own audit rows (maintenance mode).

## Decisions
- D-023 Approval transitions only through database functions, enforced by a guard trigger.
- D-024 Audit by triggers (not app code); append-only; owner-only maintenance mode.

## How to verify (run 2026-10-10)
```powershell
.\.venv\Scripts\python -m pytest            # 145 passed (73 app + 72 DB)
$env:DATA_BACKEND="supabase"; .\.venv\Scripts\uvicorn app.main:app --reload
```
Live check: planner → `/approvals` shows APR-1042 with "You submitted this plan — another approver must decide";
planner decision refused and logged. Approver → decision form, SO-1005 change `-4 d`; *Approve* → workbench and
dashboard show **v4.3 as baseline**; audit trail lists the blocked attempt, `APR-1042 status: Pending → Approved`,
`v4.2 Approved → Superseded`, `v4.3 Proposed → Approved`, each with the approver and the comment as reason.
State then restored (v4.2 baseline, APR-1042 pending, audit emptied) so the demo can be repeated.
Advisors: security — only *leaked password protection* (Auth setting); performance — "unused index" INFO only.

## Known gaps / follow-ups
- Versions are still created by the seed; the solver creates them from M09 (they enter the workflow as Draft).
- No notifications (e-mail / in-app) to approvers yet; the dashboard and `/approvals` show pending requests.
- Audit retention and export are not defined yet (keep forever for now; consider archiving in M12).
- Dev-only: test runs delete their own audit rows in maintenance mode — never do this on a production project.

## Problems hit
- **PL/pgSQL resolves record fields at run time**: `new.is_baseline` failed on `approval_requests` even behind a
  false `tg_table_name` check → branch by table and operation (troubleshooting).
- `SELECT … FOR UPDATE` also applies UPDATE policies → rows were invisible and errors misleading → plain reads.
- Windows console (cp1252) could not print `→` from a check script → `PYTHONIOENCODING=utf-8`.
