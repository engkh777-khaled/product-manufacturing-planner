# AI Valve Production Planning Agent — MVP Specification

**Version:** 1.0  
**Purpose:** Build an AI-assisted production planning application for a valve manufacturing and assembly factory.

---

## 1. Project Overview

The AI Valve Production Planning Agent helps production planners determine how to manufacture and assemble valves against customer orders while considering:

- Customer delivery dates and order priorities.
- Product configurations and approved Bills of Materials (BOMs).
- Raw materials, purchased components, and available inventory.
- Supplier purchase orders and expected delivery dates.
- Manufacturing and assembly routings.
- Machine and work-center capacity.
- Shift calendars, holidays, and planned downtime.
- Operation sequence and production lead times.
- Inspection, testing, and final assembly requirements.
- Bottlenecks, material shortages, and delivery risks.

The application should generate a feasible production plan, explain the reasoning behind its recommendations, and allow authorized users to review and approve the proposed plan.

### Primary objective

Transform customer sales orders into a practical, material-aware, capacity-constrained production schedule that helps the factory meet delivery commitments.

### Design principle

**The planning engine calculates; the AI explains, assists, and coordinates.**

The AI must not invent inventory balances, operation times, or production capacity. It must retrieve factory data and use validated planning tools for calculations.

---

## 2. MVP Objectives

The first version should allow a planner to:

1. Create and manage customer sales orders.
2. Maintain products, approved BOM revisions, and manufacturing routings.
3. Record inventory balances and incoming purchase orders.
4. Define machines, work centers, shifts, and downtime.
5. Calculate material requirements for customer orders.
6. Identify shortages and expected material availability dates.
7. Generate a production schedule based on finite capacity.
8. Detect delivery-date risks and production bottlenecks.
9. Simulate changes to priorities, quantities, material dates, and machine availability.
10. Ask the AI assistant questions about the plan.
11. Save and compare schedule versions.
12. Submit a proposed plan for human approval.
13. Maintain an audit trail of planning decisions and changes.

### MVP limitations

The MVP does not need to connect to a full ERP system. It can start with manually entered data and a small demonstration dataset.

The application must not automatically:

- Release production orders to the factory.
- Place purchase orders with suppliers.
- Commit revised delivery dates to customers.
- Approve its own planning recommendations.
- Reschedule released production without authorized review.

These capabilities can be considered later, after the planning engine has been validated.

---

## 3. Recommended Technology Stack

### Frontend

- Next.js
- TypeScript
- React
- A responsive component library
- A Gantt chart or timeline component for production scheduling

### Database and authentication

- Supabase
- PostgreSQL
- Supabase Authentication
- PostgreSQL Row Level Security (RLS)

### Planning engine

- Python
- FastAPI for the planning API
- Google OR-Tools CP-SAT for production scheduling
- Pandas for data preparation where useful

### AI assistant

Use an LLM with tool/function calling.

The AI assistant should call application tools rather than independently calculate or invent production facts.

### Deployment

- Frontend: Vercel
- Database and authentication: Supabase
- Planning API: a Python-compatible hosting service

The planning engine may be deployed separately from the Next.js application.

### High-level architecture

    User
      |
      v
    Next.js Frontend
      |
      v
    Authenticated Application API
      |
      +--------------------+
      |                    |
      v                    v
    Supabase           AI Assistant
    PostgreSQL             |
      ^                    v
      |              Validated Tools
      |                    |
      +--------------------+
                           |
                           v
                    Python Planning API
                           |
                           v
                    OR-Tools Scheduler
                           |
                           v
                    Validated Plan
                           |
                           v
                    Save Draft / Approval

The AI model must not receive unrestricted database access or bypass application authorization.

---

## 4. Core Business Workflow

The main workflow is:

1. A sales order is entered.
2. The application validates the order and product configuration.
3. The system identifies the approved BOM revision and routing.
4. The material planning engine calculates gross and net material requirements.
5. The system checks current inventory and expected incoming receipts.
6. The capacity engine identifies eligible work centers, machines, shifts, and operation durations.
7. The scheduling solver generates a proposed production schedule.
8. An independent validator checks the proposed schedule against hard constraints.
9. The application identifies shortages, bottlenecks, and delivery risks.
10. The AI assistant explains the results in plain language.
11. The planner reviews the proposed plan and may run what-if scenarios.
12. The authorized approver approves or rejects the plan.
13. The approved schedule is saved as a versioned planning baseline.

Every planning run must use a consistent snapshot of the relevant factory data.

---

## 5. User Roles and Permissions

### Planner

Can:

- Create and edit draft sales orders.
- Run material planning.
- Generate proposed production schedules.
- Run simulations.
- View shortages, bottlenecks, and delivery risks.
- Save draft planning scenarios.

Cannot approve their own plan unless explicitly authorized by company policy.

### Production Supervisor

Can:

- Review work-center and machine schedules.
- Report actual operation progress.
- Report downtime and production interruptions.
- Propose changes to operation sequencing.
- Review production feasibility.

### Stores / Inventory

Can:

- Update inventory balances.
- Record material reservations.
- Record stock movements.
- Report material availability and shortages.

### Purchasing

Can:

- Record supplier purchase orders.
- Update expected receipt dates.
- Record received quantities.
- Review material shortage reports.

### Engineering

Can:

- Maintain product definitions.
- Maintain BOM revisions.
- Maintain routing definitions.
- Submit engineering revisions for approval.

### Approver / Manager

Can:

- Review proposed plans.
- Approve or reject planning versions.
- Review delivery risks and capacity constraints.
- Review audit records.

### Administrator

Can:

- Manage users and role assignments.
- Configure reference data.
- Manage application settings.

### Security requirements

- Enforce permissions on the server, not only in the UI.
- Enable and test Supabase RLS.
- Never expose Supabase service-role credentials to the browser.
- Restrict AI tool access according to the authenticated user's permissions.
- Log consequential changes.
- Require explicit authorization for approvals.
- Prevent the AI assistant from approving plans on its own.

---

## 6. Core Database Design

The following is the initial logical schema. It should be refined during implementation.

Use UUID primary keys where appropriate, timestamps, foreign keys, validation constraints, and appropriate indexes.

### 6.1 Products

Table: `products`

Suggested fields:

- `id`
- `product_code`
- `name`
- `description`
- `product_family`
- `valve_type`
- `size`
- `pressure_class`
- `body_material`
- `trim_material`
- `connection_type`
- `product_status`
- `created_at`
- `updated_at`

Requirements:

- Product codes must be unique.
- Inactive products cannot be used for new production orders unless explicitly permitted.
- Product configuration must be sufficiently specific to determine the correct BOM and routing.

### 6.2 BOM Headers

Table: `bom_headers`

Suggested fields:

- `id`
- `product_id`
- `revision`
- `status`
- `effective_from`
- `effective_to`
- `approved_by`
- `approved_at`
- `created_at`

Possible statuses:

- Draft
- Under Review
- Approved
- Superseded
- Obsolete

Only an approved, effective BOM revision may be used for an executable planning proposal.

### 6.3 BOM Items

Table: `bom_items`

Suggested fields:

- `id`
- `bom_header_id`
- `component_product_id`
- `component_code`
- `quantity_per_parent`
- `unit_of_measure`
- `scrap_factor`
- `is_phantom`
- `is_optional`
- `operation_reference`
- `created_at`

Requirements:

- Validate that component quantities are positive.
- Detect circular BOM references.
- Support multilevel BOM explosion.
- Account for scrap and yield.
- Distinguish manufactured components from purchased components.
- Support optional components where product configuration requires them.

### 6.4 Routing Headers

Table: `routing_headers`

Suggested fields:

- `id`
- `product_id`
- `revision`
- `status`
- `effective_from`
- `effective_to`
- `approved_by`
- `approved_at`

### 6.5 Routing Operations

Table: `routing_operations`

Suggested fields:

- `id`
- `routing_header_id`
- `operation_number`
- `operation_code`
- `operation_name`
- `work_center_id`
- `setup_time_minutes`
- `cycle_time_minutes`
- `inspection_time_minutes`
- `minimum_batch_quantity`
- `maximum_batch_quantity`
- `required_skill`
- `is_mandatory`
- `created_at`

Examples of operations:

- Cutting
- Machining
- Welding
- Heat treatment
- Assembly
- Pressure testing
- Painting
- Final inspection
- Packing

Routing operations must support precedence relationships. If an operation has multiple predecessors, represent those dependencies explicitly in a separate operation-dependency table.

### 6.6 Sales Orders

Table: `sales_orders`

Suggested fields:

- `id`
- `order_number`
- `customer_name`
- `customer_reference`
- `order_date`
- `requested_delivery_date`
- `priority`
- `status`
- `currency`
- `notes`
- `created_by`
- `created_at`
- `updated_at`

Possible statuses:

- Draft
- Confirmed
- Planning
- Planned
- In Production
- Completed
- Cancelled

### 6.7 Sales Order Items

Table: `sales_order_items`

Suggested fields:

- `id`
- `sales_order_id`
- `line_number`
- `product_id`
- `product_configuration`
- `bom_revision_id`
- `routing_revision_id`
- `quantity`
- `unit_of_measure`
- `requested_delivery_date`
- `priority`
- `status`

Requirements:

- Support multiple lines per sales order.
- Allow line-specific delivery dates.
- Store the BOM and routing revisions selected for planning.
- Do not silently change an order's approved engineering configuration.

### 6.8 Warehouses

Table: `warehouses`

Suggested fields:

- `id`
- `warehouse_code`
- `name`
- `location`
- `is_active`

### 6.9 Inventory Balances

Table: `inventory_balances`

Suggested fields:

- `id`
- `product_id`
- `warehouse_id`
- `lot_number`
- `on_hand_quantity`
- `reserved_quantity`
- `quality_hold_quantity`
- `updated_at`

A basic net-available quantity is:

    Net Available =
        On Hand Quantity
        - Reserved Quantity
        - Quality Hold Quantity

The calculation must reflect the company's actual reservation and quality-hold policies.

Do not subtract reservations twice if the inventory data already represents unreserved stock.

Where traceability is required, support lot numbers, batch status, shelf life, and material certificates.

### 6.10 Purchase Orders

Table: `purchase_orders`

Suggested fields:

- `id`
- `po_number`
- `supplier_name`
- `order_date`
- `status`
- `created_at`

### 6.11 Purchase Order Items

Table: `purchase_order_items`

Suggested fields:

- `id`
- `purchase_order_id`
- `product_id`
- `ordered_quantity`
- `received_quantity`
- `expected_delivery_date`
- `status`
- `quality_status`
- `warehouse_id`

Only eligible incoming quantities should contribute to future material availability.

The system must consider:

- Remaining open quantity.
- Expected receipt date.
- Purchase order status.
- Quality inspection and release requirements.
- Whether the receipt is usable at the required production location.

### 6.12 Work Centers

Table: `work_centers`

Suggested fields:

- `id`
- `work_center_code`
- `name`
- `description`
- `department`
- `capacity_unit`
- `is_active`

Examples:

- CNC Machining
- Conventional Machining
- Welding
- Assembly
- Pressure Testing
- Painting

### 6.13 Machines and Resources

Table: `machines`

Suggested fields:

- `id`
- `machine_code`
- `name`
- `work_center_id`
- `capability_tags`
- `capacity_factor`
- `is_active`

A work center may contain multiple machines.

The scheduling engine must only assign an operation to resources that satisfy its capabilities and technical requirements.

### 6.14 Shift Calendars

Table: `shift_calendars`

Suggested fields:

- `id`
- `resource_id`
- `resource_type`
- `shift_name`
- `start_time`
- `end_time`
- `valid_from`
- `valid_to`
- `working_days`
- `capacity_factor`

Also maintain a holiday and exception calendar.

The scheduling engine must respect shifts, non-working days, holidays, and approved overtime rules.

### 6.15 Machine Downtime

Table: `machine_downtime`

Suggested fields:

- `id`
- `machine_id`
- `start_datetime`
- `end_datetime`
- `reason`
- `status`
- `created_by`

Confirmed downtime must block the affected resource during scheduling.

### 6.16 Production Orders

Table: `production_orders`

Suggested fields:

- `id`
- `production_order_number`
- `sales_order_item_id`
- `product_id`
- `quantity`
- `status`
- `planned_start`
- `planned_finish`
- `actual_start`
- `actual_finish`
- `schedule_version_id`

### 6.17 Production Operations

Table: `production_operations`

Suggested fields:

- `id`
- `production_order_id`
- `routing_operation_id`
- `operation_number`
- `quantity`
- `status`
- `planned_start`
- `planned_finish`
- `actual_start`
- `actual_finish`
- `assigned_machine_id`

### 6.18 Planning Runs

Table: `planning_runs`

Suggested fields:

- `id`
- `run_number`
- `run_type`
- `planning_horizon_start`
- `planning_horizon_end`
- `input_snapshot`
- `algorithm_version`
- `solver_status`
- `objective_value`
- `started_at`
- `completed_at`
- `created_by`

### 6.19 Schedule Versions

Table: `schedule_versions`

Suggested fields:

- `id`
- `planning_run_id`
- `version_number`
- `status`
- `is_baseline`
- `created_by`
- `created_at`
- `approved_by`
- `approved_at`
- `change_summary`

Possible statuses:

- Draft
- Proposed
- Approved
- Rejected
- Superseded

Every planning run must produce an identifiable result. Do not overwrite the approved schedule when testing a new scenario.

### 6.20 Alerts

Table: `planning_alerts`

Suggested fields:

- `id`
- `planning_run_id`
- `sales_order_item_id`
- `production_order_id`
- `alert_type`
- `severity`
- `description`
- `recommended_action`
- `status`
- `created_at`

Examples:

- Material shortage
- Late supplier receipt
- Capacity overload
- Missing BOM
- Missing routing
- Delivery date at risk
- Machine downtime conflict
- Infeasible schedule

### 6.21 Approval Requests

Table: `approval_requests`

Suggested fields:

- `id`
- `schedule_version_id`
- `requested_by`
- `approver_id`
- `status`
- `comments`
- `requested_at`
- `decided_at`

### 6.22 Audit Logs

Table: `audit_logs`

Suggested fields:

- `id`
- `user_id`
- `action`
- `entity_type`
- `entity_id`
- `before_data`
- `after_data`
- `reason`
- `created_at`

Audit important changes, especially approvals, schedule changes, engineering revisions, inventory adjustments, and overrides of planning recommendations.

---

## 7. Material Requirements Planning

The material planning engine must calculate requirements from the selected BOM and order quantity.

### 7.1 BOM explosion

For every sales order line:

1. Identify the approved and effective BOM revision.
2. Calculate the required quantity of each component.
3. Apply scrap and yield factors.
4. Expand manufactured subassemblies recursively.
5. Consolidate shared component demand across orders.
6. Preserve the relationship between demand and the sales order or production order that created it.

A simplified formula is:

    Required Component Quantity =
        Parent Production Quantity
        × Component Quantity per Parent
        / Expected Yield

Use a clearly defined yield convention. Do not apply the same scrap factor twice.

### 7.2 Net material requirements

Calculate:

    Net Requirement =
        Gross Requirement
        - Eligible Inventory
        - Eligible Scheduled Receipts
        + Applicable Reservations or Allocations

The exact formula must follow a consistent reservation and allocation model. Do not double-count inventory, reservations, or receipts.

Calculate material availability by date, not just by the current balance.

### 7.3 Time-phased material availability

For every required component, determine:

- Current usable inventory.
- Existing allocations.
- Open purchase order quantities.
- Expected receipt dates.
- Inspection and release lead time.
- Internal manufacturing lead time, if applicable.
- The earliest date the material is available for the relevant operation.

A component expected to arrive next week must not be treated as available today.

### 7.4 Material shortage alerts

Create alerts for:

- Insufficient current stock.
- Expected receipts after the required date.
- Missing supplier delivery dates.
- Material on quality hold.
- Unapproved or obsolete components.
- Missing BOM items.
- Incorrect or incompatible units of measure.

Each alert should explain:

- Which order is affected.
- Which component is missing.
- The required quantity.
- The available quantity.
- The shortage quantity.
- The required availability date.
- The expected availability date, if known.
- The potential impact on the production schedule.
- Possible corrective actions.

### 7.5 Corrective actions

Examples include:

- Expedite a supplier delivery.
- Transfer eligible inventory from another warehouse.
- Reallocate available components between orders.
- Reschedule an order.
- Use an approved alternative component.
- Split an order into feasible batches.

The system must never substitute an unapproved engineering component automatically.

---

## 8. Finite-Capacity Production Scheduling

The scheduler must produce a feasible sequence of manufacturing and assembly operations.

A simple date calculation based only on average lead time is not sufficient.

### 8.1 Scheduling inputs

The scheduler should consider:

- Sales order priorities.
- Requested delivery dates.
- Production quantities.
- Approved routing revisions.
- Operation precedence.
- Setup times.
- Cycle times.
- Batch sizes.
- Eligible machines and work centers.
- Shift calendars.
- Holidays.
- Confirmed machine downtime.
- Existing production commitments.
- Material availability dates.
- Inspection and testing operations.
- Frozen or released operations.
- Approved overtime capacity.

### 8.2 Operation duration

A simplified duration estimate is:

    Operation Duration =
        Setup Time
        + (Quantity × Cycle Time per Unit)

If batch processing is used, calculate the duration using the actual batch model.

The implementation must define whether setup time is charged per order, per batch, or per changeover.

### 8.3 Hard constraints

The scheduler must enforce:

1. An operation cannot start before its required predecessor finishes.
2. An operation cannot start before required materials are available.
3. A machine cannot process incompatible operations simultaneously.
4. An operation can only use a qualified resource.
5. Operations must respect working calendars and downtime.
6. Existing frozen commitments must be preserved.
7. Mandatory inspections and testing must be included.
8. A production order cannot be marked complete before all required operations are complete.
9. The schedule must respect any applicable batch or resource restrictions.
10. All proposed operation times must pass independent validation.

### 8.4 Scheduling objectives

Use a configurable weighted objective, such as:

- Minimize weighted delivery lateness.
- Minimize the number of late orders.
- Minimize unnecessary changes to an approved baseline.
- Minimize overtime where practical.
- Reduce avoidable machine idle time.
- Prioritize urgent orders according to defined business rules.

Avoid optimizing machine utilization at the expense of critical delivery commitments without making that trade-off explicit.

### 8.5 Solver

Use Google OR-Tools CP-SAT for the initial scheduling engine.

Model:

- Interval variables for operations.
- Precedence constraints.
- Optional intervals for alternative machines.
- No-overlap constraints for exclusive resources.
- Calendar availability.
- Material readiness constraints.
- Due-date penalties.
- Baseline-change penalties where appropriate.

The exact mathematical model should be developed incrementally and tested against representative factory scenarios.

### 8.6 Planning horizon

Start with a configurable rolling horizon, for example four weeks.

Allow the planner to define:

- Planning start date.
- Planning end date.
- Included orders.
- Frozen operations.
- Priority rules.
- Overtime policy.
- Scenario assumptions.

### 8.7 Solver outcomes

Return one of the following planning outcomes:

- **Feasible:** All hard constraints are satisfied.
- **Feasible with lateness:** The schedule is valid, but one or more delivery dates are missed.
- **Needs data:** Required BOM, routing, material, calendar, or resource information is missing.
- **Infeasible:** No schedule satisfying the configured hard constraints was found.
- **Solver limit reached:** The solver stopped before proving feasibility or infeasibility.

Do not claim that a schedule is impossible solely because the solver reached its time limit.

### 8.8 Independent schedule validation

After scheduling, run a separate validation function to check:

- Operation precedence.
- Machine overlap.
- Work-center capacity.
- Shift and calendar restrictions.
- Downtime conflicts.
- Material availability.
- Required inspections and testing.
- Frozen commitments.
- Delivery-date calculations.
- Invalid or missing assignments.

A schedule that fails validation must not be approved.

---

## 9. Delivery Risk Analysis

For every sales order line, calculate:

- Requested delivery date.
- Planned completion date.
- Estimated lateness.
- Material readiness date.
- Critical-path operations.
- Main bottleneck.
- Major material shortages.
- Confidence or data-quality indicators.

Use explicit rules to classify risk.

Example:

- **Low risk:** Feasible schedule with sufficient margin and no unresolved critical shortages.
- **Medium risk:** Feasible schedule with limited margin or manageable dependencies.
- **High risk:** Expected lateness, unresolved critical material shortage, or major capacity conflict.
- **Unknown:** Insufficient or unreliable data to estimate delivery feasibility.

Risk thresholds should be configurable. Do not present subjective AI confidence as a statistically calibrated probability.

The AI assistant should explain the factors driving the risk and identify actions that could reduce it.

---

## 10. What-If Simulation

The planner must be able to create a scenario without changing the approved schedule.

Example scenarios:

- A supplier delivery is delayed by five days.
- A critical machine is unavailable for two shifts.
- An urgent order is added.
- An order quantity increases by 20%.
- Overtime is enabled for one work center.
- A second machine becomes available.
- A purchase order arrives earlier.
- One customer order receives a higher priority.

For each scenario, show:

- Baseline schedule.
- Proposed schedule.
- Orders whose delivery dates change.
- New material shortages.
- Changes in utilization and bottlenecks.
- Additional overtime, where applicable.
- Reasons for the differences.

Save scenarios as separate versions. Do not overwrite the baseline.

---

## 11. AI Assistant Design

The AI assistant should be a conversational interface to validated planning functions.

It should answer questions such as:

- Can we deliver this order by the requested date?
- Why is this order late?
- Which components are blocking production?
- Which machine is the current bottleneck?
- What happens if a critical supplier is delayed?
- Which orders are most at risk?
- Can we improve delivery performance by enabling overtime?
- What changed between the last two schedules?
- Why did the scheduler assign this operation to this machine?

### 11.1 Suggested tools

Implement server-side tools such as:

- `get_order_details`
- `validate_order_readiness`
- `calculate_material_requirements`
- `check_material_availability`
- `get_production_capacity`
- `get_schedule_status`
- `generate_production_schedule`
- `simulate_order_change`
- `explain_delivery_risk`
- `save_proposed_plan`
- `submit_plan_for_approval`

Each tool must have:

- A documented input schema.
- A documented output schema.
- Input validation.
- Authorization checks.
- Clear error handling.
- Logging for consequential actions.
- Idempotency where appropriate for write operations.

### 11.2 Example conversation

**User:** Can we deliver sales order SO-1005 by 30 November?

**Agent:** I will check the approved BOM, material availability, routing, and available production capacity before assessing the requested date.

The agent calls the appropriate tools.

**Agent:** The current proposed schedule finishes on 3 December, three days after the requested date. The main constraints are a late actuator delivery and a bottleneck at pressure testing. I can simulate an expedited actuator delivery and an additional testing shift to estimate whether the requested date is achievable.

The example dates and results above are illustrative only. The real application must derive them from factory data.

### 11.3 AI system instructions

Use instructions similar to the following:

"You are an AI production planning assistant for a valve manufacturing and assembly factory.

Your role is to help planners understand orders, materials, capacity, scheduling results, and delivery risks.

Rules:

1. Never invent inventory quantities, operation durations, supplier dates, machine capacity, or production status.
2. Use authorized application tools for authoritative calculations.
3. Identify missing, stale, or inconsistent data.
4. Never schedule an operation before required materials and predecessor operations are ready.
5. Never assign operations beyond resource capacity or outside permitted calendars.
6. Distinguish verified facts, planning assumptions, and recommendations.
7. Explain delivery risks using the actual constraints found.
8. Use simulation tools for proposed changes.
9. Never place a purchase order or release production automatically in the MVP.
10. Never approve your own plan.
11. Do not commit a delivery date to a customer.
12. Obtain explicit human authorization before consequential changes.
13. If the planning solver cannot find a solution, report the solver outcome accurately.
14. Prefer traceable, explainable recommendations over unsupported claims."

---

## 12. User Interface

### 12.1 Dashboard

Show:

- Open sales orders.
- Orders due soon.
- Orders at delivery risk.
- Material shortages.
- Critical bottlenecks.
- Current schedule status.
- Work-center utilization.
- Plans awaiting approval.

### 12.2 Sales Orders

Provide:

- Order list.
- Order details.
- Order lines and quantities.
- Requested delivery dates.
- Priority.
- Product configuration.
- BOM and routing revision.
- Readiness validation.
- Planning status.

### 12.3 Product and BOM Management

Provide:

- Product catalogue.
- BOM structure.
- BOM revision history.
- Component quantities.
- Engineering approval status.
- BOM validation.
- Missing-component warnings.

### 12.4 Inventory and Purchasing

Provide:

- Inventory balances.
- Reservations.
- Quality holds.
- Incoming purchase orders.
- Expected receipt dates.
- Material shortage lists.
- Time-phased availability.

### 12.5 Resource and Calendar Management

Provide:

- Work centers.
- Machines.
- Machine capabilities.
- Routing assignments.
- Shifts.
- Holidays.
- Planned downtime.
- Approved overtime.

### 12.6 Planning Workbench

This is the core screen.

Provide:

- A Gantt-style production schedule.
- Orders and operation sequences.
- Machine and work-center lanes.
- Material readiness indicators.
- Delivery risk indicators.
- Conflict and shortage alerts.
- Baseline-versus-scenario comparison.
- Controls to generate and validate a plan.
- Controls to save a draft and submit it for approval.

### 12.7 AI Assistant

Provide a chat interface that can reference:

- Selected order.
- Current planning run.
- Current schedule version.
- Relevant shortage and risk alerts.

The assistant should link explanations to the affected orders, operations, and components.

### 12.8 Approvals and Audit

Provide:

- Plans awaiting approval.
- Plan summary.
- Schedule changes.
- Material and capacity risks.
- Approve/reject actions for authorized users.
- Approval comments.
- Audit history.

---

## 13. Sample Demonstration Dataset

Start with a small dataset that can be reviewed manually.

Suggested records:

- 10–20 products.
- 2–3 product families.
- 2–4 BOM levels where appropriate.
- 20–40 component types.
- 5–10 sales orders.
- 5–10 work centers.
- 10–20 machines or production resources.
- 2–3 shifts where applicable.
- Several open supplier purchase orders.
- A small number of material shortages.
- A few machine downtime events.

Include representative operations such as machining, welding, assembly, pressure testing, painting, and final inspection.

Make it possible to demonstrate both feasible and infeasible planning cases.

All sample data must be clearly labelled as fictional.

---

## 14. Acceptance Tests

The MVP must pass the following tests.

### Test 1: Normal order

Given:

- An approved BOM.
- An approved routing.
- Sufficient material.
- Available production capacity.
- A reasonable requested delivery date.

Expected result:

- Material requirements are calculated correctly.
- A feasible schedule is generated.
- The schedule passes independent validation.
- The plan can be saved and submitted for approval.

### Test 2: Missing critical component

Given:

- An order requiring a component.
- Insufficient stock.
- No eligible incoming receipt before the required date.

Expected result:

- The shortage is identified.
- The affected operation or order is linked to the shortage.
- The schedule does not falsely assume the component is available.
- The system explains the delivery impact.

### Test 3: Late supplier receipt

Given:

- A component expected after its required date.

Expected result:

- Material availability is calculated using the expected receipt date.
- The affected production operation cannot start before material readiness.
- The delivery risk is recalculated.

### Test 4: Machine conflict

Given:

- Two operations requiring the same exclusive machine.

Expected result:

- The operations do not overlap.
- The schedule validator detects any invalid overlap.
- A valid alternative machine may be used if qualified and available.

### Test 5: Urgent order

Given:

- A new high-priority order competing for resources with existing orders.

Expected result:

- The scheduler evaluates the priority according to configured business rules.
- The system identifies any resulting delivery-date changes.
- Existing approved commitments are handled according to the configured freeze policy.

### Test 6: Machine downtime

Given:

- Confirmed downtime during a planned operation.

Expected result:

- The operation is not scheduled during the blocked interval.
- The schedule is adjusted or reported as infeasible.

### Test 7: Impossible requested date

Given:

- A requested delivery date earlier than the earliest feasible completion date.

Expected result:

- The system reports that the requested date cannot currently be achieved under the modeled constraints.
- The report identifies the limiting factors.
- The system does not fabricate a feasible schedule.

### Test 8: Missing engineering data

Given:

- A product without an approved BOM or routing.

Expected result:

- The order is marked as needing data.
- The system identifies the missing information.
- The order is not treated as production-ready.

### Test 9: Approved plan followed by a receipt change

Given:

- An approved schedule.
- A subsequent change to a critical supplier receipt.

Expected result:

- The approved schedule remains preserved as the baseline.
- The system flags the potential impact.
- A new planning run or scenario can be generated.
- Approval is required before the new schedule becomes the approved version.

### Test 10: Competing orders

Given:

- Multiple orders competing for the same critical component.

Expected result:

- The material allocation logic is consistent and explicit.
- The same stock is not allocated twice.
- The system explains which orders are affected by the allocation decision.

---

## 15. Development Roadmap

### Phase 1: Foundation and Master Data

Build:

- Project structure.
- Authentication and roles.
- Database migrations.
- Products.
- BOMs and revisions.
- Routings.
- Sales orders.
- Inventory.
- Purchase orders.
- Machines and work centers.
- Calendars.

Deliverable:

A working application with realistic, validated factory data.

### Phase 2: Material Planning

Build:

- Multilevel BOM explosion.
- Scrap and yield calculations.
- Net requirements.
- Time-phased material availability.
- Shortage alerts.
- Material readiness dates.

Deliverable:

A tested material planning engine independent of the AI assistant.

### Phase 3: Production Scheduling

Build:

- Operation-duration calculation.
- Resource eligibility.
- Precedence constraints.
- Resource calendars.
- Machine downtime.
- Finite-capacity scheduling.
- Delivery lateness objectives.
- Independent schedule validation.

Deliverable:

A scheduler that produces valid plans for representative test cases.

### Phase 4: Planning Workbench

Build:

- Dashboard.
- Schedule table.
- Gantt chart.
- Material shortage view.
- Bottleneck and delivery risk views.
- Schedule versioning.
- Scenario comparison.

Deliverable:

A planner can generate and review plans without using AI chat.

### Phase 5: AI Integration

Build:

- AI assistant.
- Structured tool calling.
- Order and schedule explanations.
- Delivery risk analysis.
- What-if simulation requests.
- Plan summaries.
- Permission-aware actions.

Deliverable:

The AI assistant helps users operate and understand the planning system without replacing the deterministic planning engine.

### Phase 6: Pilot and Hardening

Complete:

- Data validation.
- Authorization testing.
- RLS testing.
- Audit logging.
- Solver performance tests.
- Backup and recovery checks.
- User acceptance testing.
- Documentation.
- Pilot with real factory data.

Deliverable:

A controlled MVP ready for supervised factory use.

---

## 16. Suggested Project Structure

One possible structure is:

    valve-planning-agent/
    ├── app/
    │   ├── dashboard/
    │   ├── orders/
    │   ├── products/
    │   ├── bom/
    │   ├── inventory/
    │   ├── capacity/
    │   ├── schedule/
    │   ├── approvals/
    │   └── agent/
    ├── components/
    │   ├── ui/
    │   ├── orders/
    │   ├── planning/
    │   └── gantt/
    ├── lib/
    │   ├── supabase/
    │   ├── auth/
    │   ├── api/
    │   └── validation/
    ├── planning-service/
    │   ├── api/
    │   ├── material_planning/
    │   ├── scheduling/
    │   ├── validation/
    │   └── tests/
    ├── supabase/
    │   ├── migrations/
    │   └── seed.sql
    ├── docs/
    │   ├── architecture.md
    │   ├── database-schema.md
    │   ├── scheduling-model.md
    │   └── acceptance-tests.md
    ├── .env.example
    ├── README.md
    └── package.json

The exact structure can change depending on the starter template and deployment strategy.

---

## 17. KPIs

Track:

- On-time delivery rate.
- Number of late orders.
- Average order lateness.
- Material-related production delays.
- Number and age of material shortages.
- Work-center utilization.
- Bottleneck waiting time.
- Schedule adherence.
- Number of schedule changes.
- Number of manual overrides.
- Planning run duration.
- Solver failure rate.
- Percentage of plans passing independent validation.
- Accuracy of planned versus actual operation durations.

Where actual production data is available, compare planned results against actual outcomes.

---

## 18. Important Design Decisions

### Deterministic calculations first

Material planning and scheduling must be implemented and tested independently of the LLM.

### Human approval

The AI proposes and explains. Authorized people approve consequential plans.

### Traceability

Every plan must be traceable to its inputs, engineering revisions, assumptions, and algorithm version.

### Time-phased material planning

Current inventory alone is not enough. Incoming receipts and required dates must be considered.

### Finite capacity

The scheduler must model real resource availability rather than assume unlimited machine capacity.

### Engineering revision control

An order must use the correct approved product configuration, BOM, and routing.

### Explainability

The system must explain why an order is late or why a resource was selected using actual planning results.

### Data quality

Missing or inconsistent data must produce explicit warnings, not invented defaults.

### Security

Authorization and database access must be enforced by the backend. AI tool calls must not bypass user permissions.

### Incremental development

Build and validate the material planning and scheduling engines before adding sophisticated AI behavior.

---

## 19. Recommended First Implementation Task

Do not ask an AI coding tool to build the entire application in one prompt.

Start with:

1. Create the project structure.
2. Create the Supabase schema and migrations.
3. Add realistic fictional seed data.
4. Implement products, BOMs, routings, sales orders, inventory, and resources.
5. Add validation and tests for the master data.
6. Implement and test the material requirements calculator.
7. Only then implement the production scheduling engine.

### First milestone

The first milestone is complete when the application can accept a sales order, identify the approved BOM, calculate component requirements, compare those requirements with time-phased inventory and receipts, and report shortages accurately.

### Second milestone

The second milestone is complete when the application can schedule manufacturing operations against finite capacity, validate the result, identify delivery risks, and save a draft schedule.

### Third milestone

The third milestone is complete when the AI assistant can query the planning engine, explain results, run what-if scenarios, and submit a draft plan for authorized approval.

**The objective is not simply to build a chatbot for production planning. It is to build a reliable planning system with an AI assistant as its interface.**