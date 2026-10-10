# M11 — AI assistant with tool calling

| | |
|---|---|
| **Date** | 2026-10-10 |
| **Author** | Khaled (implemented with Claude Code) |
| **Status** | Done (live LLM check pending an API key) |
| **Commits** | _see git log — "M11" commit_ |
| **Spec sections** | §11 (assistant design, tools §11.1, system instructions §11.3), §12.7 (assistant UI), §5 (roles) |

## Goal
Replace the scripted assistant with an LLM that answers only from authorised planning tools, runs as the signed-in
user, and can never approve, release, purchase or commit dates.

## What was done

**Tools — [app/agent/tools.py](../../app/agent/tools.py)** (the 11 tools of spec §11.1)
| Tool | Uses | Roles |
|---|---|---|
| `get_order_details` | order, lines, BOM/routing status, baseline completion, MRP staging | any |
| `validate_order_readiness` | scheduler inputs (BOM, routing, qualified machines, calendars), completed/committed ops, material gates | any |
| `calculate_material_requirements` | MRP requirements of the order (allocation, shortage, ready date) | any |
| `check_material_availability` | stock, QC holds, receipts, time-phased projection, competing demand | any |
| `get_production_capacity` | machines, capabilities, shifts, downtime, calendar, version loads, bottleneck | any |
| `get_schedule_status` | versions (baseline, drafts, scenarios), validation, delivery lines, pending approvals | any |
| `generate_production_schedule` | CP-SAT + validator on the current data — **not saved** | planner/admin |
| `simulate_order_change` | M10 scenario engine — **not saved** | planner/admin |
| `explain_delivery_risk` | fresh plan + MRP, spec §9 rules (High / Medium < 48 h margin / Low / Unknown) | any |
| `save_proposed_plan` | **pending action** → human Confirm → solve + `record_schedule_run` | planner/admin |
| `submit_plan_for_approval` | **pending action** → human Confirm → `submit_plan_for_approval()` | planner/admin |

Each tool: pydantic input model (= JSON schema sent to the model and the validator), documented output schema,
role check, `ToolError` messages for the model, unexpected errors reported without details, logging of every
call. Tools read the request's dataset — the user's RLS snapshot — so the assistant sees exactly what the user may
see. A fresh plan is cached per snapshot for 5 minutes (one CP-SAT run serves several questions).

**Human authorization (spec §11.3 rule 12)** — consequential tools only register a pending action; the UI shows
a confirmation card; **Confirm** runs it as the human (their token, RLS, workflow functions, audit app event).
Same proposal twice → one action; confirming twice → first result (idempotent); another user cannot confirm it.
There is no tool that approves, releases, places/changes POs, deletes or commits dates (tested).

**Loop — [app/agent/loop.py](../../app/agent/loop.py)** — Anthropic Messages API tool-use loop: system prompt
= spec §11.3 rules + working style (facts / assumptions / recommendations, every number from a tool) + context
(user and roles, planning date, baseline, selected order and version); ≤ 8 tool rounds; tool results ≤ 20 000
chars; history trimmed only at user prompts; API failures reported with no partial history kept; model output
rendered as escaped minimal markdown with order links.

**App** — Assistant page in agent mode when `ANTHROPIC_API_KEY` is set: real tool trace (name, input, result
summary, seconds, ✓/✗), answer, confirmation cards, "working" indicator, suggested questions from spec §11. Without
a key the scripted demo is unchanged. Settings: `ANTHROPIC_API_KEY` (server-side only, never in templates),
`ASSISTANT_MODEL` (default `claude-opus-5-5`), `ASSISTANT_MAX_TOOL_ROUNDS`, `ASSISTANT_PLAN_SECONDS`.

**Live tool check** (dev project, as planner, no LLM): all read tools return real data (≤ 5 KB each);
SO-1005 not production-ready (SR-ST6-10 short 3); simulation of PO-8821 −3 d runs; submitting what-if v4.5 is
refused.

**Tests** (272 passing: 181 app/engine + 91 DB) — [tests/test_assistant_tools.py](../../tests/test_assistant_tools.py)
(25): spec tool set and schemas, no forbidden tools, validation messages, role checks for every planner tool,
no leaked errors, results from the recorded data (orders, readiness, MRP, availability, capacity, versions, read-only
schedule and simulation, risk rules), confirmation/idempotency/ownership of actions, submit refusals, the loop with
a mocked LLM (tool results round-trip, unknown/unauthorized calls returned as errors, round limit, API failure),
escaping, the page never contains the key, Confirm flow.

## Decisions
- D-034 The assistant acts only through tools; consequential tools need a human Confirm.
- D-035 Model and key are server-side settings; without a key the scripted demo stays.

## How to verify (run 2026-10-10)
```powershell
.\.venv\Scripts\python -m pytest tests/test_assistant_tools.py    # 25 passed (mocked LLM)
.\.venv\Scripts\python -m pytest                                  # 272 passed
```
Manual (needs `ANTHROPIC_API_KEY=` in `.env`, `DATA_BACKEND=supabase`, planner sign-in): Assistant → "Can we deliver
SO-1005 by 30 November?" → tool calls (order details, risk, MRP…) and an answer matching the data: line 1 blocked at
OP-030 (seat rings short 3, QC hold could cover), line 2 on time Nov 28 waiting for PO-8821. Then "Save this plan" →
confirmation card → Confirm → Draft version.

## Known gaps / follow-ups
- **Live LLM check not run yet** (no API key in `.env`); run the manual check above once a key is added.
- Conversations and pending actions are in memory (lost on restart, one instance) — M12.
- No streaming; a question that needs a fresh plan takes ~10–20 s.
- Assistant answers are not stored in the audit log (only confirmed actions are); add conversation logging if
  required by policy.
- The right-hand evidence panel of the assistant page is still the designed static view.

## Problems hit
- Full-suite DB failures: the M06 API test edits a PO date through PostgREST (committed), which now fires the
  baseline-impact trigger; its cleanup only removed audit rows → test alerts accumulated in the dev DB. Cleanup
  extended; three leftover test alerts deleted; DB tests filter on PO-8821.
