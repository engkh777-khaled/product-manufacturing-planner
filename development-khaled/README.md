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
| M04 | 2026-10-10 | [Database schema, constraints & seed data](milestones/M04-database-schema-and-seed.md) | Done | `a25b75f` |
| M05 | 2026-10-10 | [Authentication, roles & RLS](milestones/M05-auth-roles-rls.md) | Done | `8579b2e` |
| M06 | 2026-10-10 | [Supabase repository & master-data editing](milestones/M06-supabase-repository-and-editing.md) | Done | `f2ab6c4` |
| M07 | 2026-10-10 | [Approvals & audit trail](milestones/M07-approvals-and-audit.md) | Done | `038c51f` |
| M08 | 2026-10-10 | [Material requirements planning engine](milestones/M08-mrp-engine.md) | Done | `d40a04e` |
| M09 | 2026-10-10 | [Finite-capacity scheduler & validator](milestones/M09-finite-capacity-scheduler.md) | Done | `fb15595` |
| M10 | 2026-10-10 | [Live workbench & what-if scenarios](milestones/M10-scenarios-and-live-workbench.md) | Done | `54354b2` |
| M11 | 2026-10-10 | [AI assistant with tool calling](milestones/M11-ai-assistant-tools.md) | Done (live check passed) | `ce12a90` |
| M12 | 2026-10-10 | [Hardening](milestones/M12-hardening.md) (deployment pending decisions) | Hardening done | `3db83fd` |

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
