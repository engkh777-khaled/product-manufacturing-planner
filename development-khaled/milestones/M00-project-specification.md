# M00 — Project specification

| | |
|---|---|
| **Date** | 2026-10-10 |
| **Author** | Khaled |
| **Status** | Done |
| **Commits** | `b7e2f07` (first commit) |
| **Spec sections** | All |

## Goal
Define the MVP for an AI valve production planning agent and obtain UI designs.

## What was done
- Wrote the MVP specification: [valve-planning-agent-mvp.md](../../valve-planning-agent-mvp.md).
  Key contents: business workflow (§4), roles (§5), logical schema of 22 tables (§6), MRP rules (§7),
  finite-capacity scheduling with OR-Tools CP-SAT (§8), delivery risk rules (§9), what-if simulation
  (§10), AI assistant tools and guardrails (§11), screens (§12), acceptance tests (§14), roadmap (§15).
- Produced five screen designs plus a design system in Google Stitch, exported as
  `stitch_product_manufacturing_planner.zip` (kept locally, not committed; extracted copy in
  [design/stitch/](../../design/stitch/)):
  - `executive_dashboard_operations_delivery_risk`
  - `planning_workbench_finite_capacity_gantt`
  - `sales_orders_demand_management`
  - `engineering_bom_master_data`
  - `ai_planning_assistant_diagnostic_workbench`
  - `precision_industrial_operations/DESIGN.md` (colors, typography, spacing, component rules)

## Decisions
- None recorded yet (decision log starts at M01).

## Known gaps / follow-ups
- Spec §3 recommends Next.js; this was later changed to Python (see D-002 in M01).
