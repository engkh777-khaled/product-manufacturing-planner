# Development Log — Product Manufacturing Planner

This folder is the running engineering record of the project. Read it before you change anything:
it explains **what exists, why it was built that way, and what is still missing**.

Every milestone adds a file here. Nothing is deleted: if a decision is reversed, add a new entry that supersedes the old one.

## Start here

| Read | When |
|---|---|
| [project-overview.md](project-overview.md) | First day on the project: goal, architecture, stack, folder map, how to run |
| [milestones/](milestones/) | To see what was done, in order |
| [backend-roadmap.md](backend-roadmap.md) | To see what comes next: Supabase + Python milestones, each with a test gate you run on your machine |
| [decisions.md](decisions.md) | Before changing the stack, data model, or a convention |
| [troubleshooting.md](troubleshooting.md) | When setup, builds, or `git push` misbehave |

## Milestone timeline

| # | Date | Milestone | Status | Commit |
|---|---|---|---|---|
| M00 | 2026-10-10 | [Project specification](milestones/M00-project-specification.md) | Done | `b7e2f07` |
| M01 | 2026-10-10 | [Frontend from Stitch designs](milestones/M01-frontend-from-stitch-designs.md) | Done | `afb6ce1` |
| M02 | 2026-10-10 | [GitHub repository & push setup](milestones/M02-github-repository.md) | Done | `afb6ce1` |
| M03 | 2026-10-10 | [Dev environment & configuration (Supabase cloud)](milestones/M03-dev-environment-supabase-cloud.md) | Done | `14092f0` |
| M04 | — | [Database schema, constraints & seed data](backend-roadmap.md#m04--database-schema-constraints--seed-data-spec-6-phase-1) | Next | — |
| M05 | — | [Authentication, roles & RLS](backend-roadmap.md#m05--authentication-roles--row-level-security-spec-5) | Planned | — |
| M06 | — | [Supabase repository & master-data editing](backend-roadmap.md#m06--supabase-repository--master-data-editing) | Planned | — |
| M07 | — | [Approvals & audit trail](backend-roadmap.md#m07--approvals--audit-trail-spec-621622-128) | Planned | — |
| M08 | — | [Material requirements planning engine](backend-roadmap.md#m08--material-requirements-planning-engine-spec-7-phase-2) | Planned | — |
| M09 | — | [Finite-capacity scheduler & validator](backend-roadmap.md#m09--finite-capacity-scheduler--independent-validator-spec-8-phase-3) | Planned | — |
| M10 | — | [Live workbench & what-if scenarios](backend-roadmap.md#m10--planning-workbench-on-live-schedules--what-if-scenarios-spec-10-126-phase-4) | Planned | — |
| M11 | — | [AI assistant with tool calling](backend-roadmap.md#m11--ai-assistant-with-tool-calling-spec-11-phase-5) | Planned | — |
| M12 | — | [Hardening & deployment](backend-roadmap.md#m12--hardening--deployment-spec-phase-6) | Planned | — |

## How to add a milestone

1. Copy [milestones/_template.md](milestones/_template.md) to `milestones/MNN-short-name.md` (next number, kebab-case name).
2. Fill in every section. Write facts you verified (commands run, test results), not intentions.
3. Add a row to the **Milestone timeline** above.
4. Record any new architectural choice in [decisions.md](decisions.md) and any new gotcha in [troubleshooting.md](troubleshooting.md).
5. If the folder map, stack, or run instructions changed, update [project-overview.md](project-overview.md).
6. Commit the docs together with (or right after) the code they describe.

## Conventions

- Dates are absolute (`YYYY-MM-DD`), never "yesterday" or "last week".
- Link code with relative paths, e.g. [`app/planning.py`](../app/planning.py).
- Reference the spec by section, e.g. "spec §8.3", where the spec is [valve-planning-agent-mvp.md](../valve-planning-agent-mvp.md).
- All sample data in this project is **fictional** (spec §13). Say so whenever you show numbers.
