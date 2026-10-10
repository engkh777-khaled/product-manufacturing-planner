"""Domain models mirroring the logical schema in valve-planning-agent-mvp.md §6.

Only the fields the UI needs are modelled; they map 1:1 onto future Supabase tables.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field


class OrderStatus(str, Enum):
    DRAFT = "Draft"
    CONFIRMED = "Confirmed"
    PLANNING = "Planning"
    PLANNED = "Planned"
    IN_PRODUCTION = "In Production"
    COMPLETED = "Completed"
    CANCELLED = "Cancelled"


class Priority(str, Enum):
    CRITICAL = "Critical"
    HIGH = "High"
    NORMAL = "Normal"
    LOW = "Low"


class RiskLevel(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    UNKNOWN = "Unknown"


class ItemType(str, Enum):
    ASSY = "ASSY"
    SUB_ASSY = "SUB-ASSY"
    MFG = "MFG"
    PUR = "PUR"


# --- Engineering -------------------------------------------------------------


class Product(BaseModel):
    product_code: str
    name: str
    family: str
    valve_type: str
    size: str
    pressure_class: str
    body_material: str
    trim_material: str
    connection_type: str
    status: str = "Active"
    design_standard: str = ""
    fire_safe: str = ""
    sour_service: str = ""
    hydro_test_pressure: str = ""
    std_lead_time_days: float = 0


class BomItem(BaseModel):
    """One node of a multilevel BOM (§6.3). `parent` is None for the top assembly."""

    id: str
    parent: str | None
    component_code: str
    name: str
    material: str = ""
    item_type: ItemType
    quantity_per_parent: float = Field(gt=0)
    unit_of_measure: str = "EA"
    scrap_factor: float = 0.0  # fraction, e.g. 0.02 = 2 %
    on_hand: float | None = None
    reserved: float = 0
    quality_hold: float = 0
    inventory_note: str = ""
    incoming_po: str | None = None


class BomHeader(BaseModel):
    product_code: str
    revision: str
    status: str  # Draft | Under Review | Approved | Superseded | Obsolete
    effective_from: date
    approved_by: str
    eco_number: str
    plm_source: str
    items: list[BomItem]


class RoutingOperation(BaseModel):
    operation_number: str
    name: str
    work_center: str
    setup_minutes: int = 0
    cycle_minutes: int = 0
    duration_label: str
    predecessor: str
    note: str = ""
    min_batch: int | None = None
    is_mandatory_gate: bool = False
    gate_detail: str = ""


class RoutingHeader(BaseModel):
    product_code: str
    revision: str
    status: str
    operations: list[RoutingOperation]


# --- Demand -------------------------------------------------------------------


class SalesOrderLine(BaseModel):
    line_number: int
    description: str
    quantity: int
    tags: str
    note: str


class MaterialStage(BaseModel):
    name: str
    staged: int
    required: int
    note: str = ""
    blocked_by: str | None = None


class SalesOrder(BaseModel):
    order_number: str
    customer_name: str
    customer_reference: str
    customer_profile: str
    customer_otd: float
    product_code: str
    spec_title: str
    spec_detail: str
    tag: str
    quantity: int
    lines: list[SalesOrderLine]
    bom_revision: str
    bom_status: str
    routing_revision: str
    work_centers: str
    order_date: date
    requested_delivery_date: date
    projected_completion: date | None  # None = needs data
    priority: Priority
    status: OrderStatus
    value_usd: float
    limiting_constraint: str = ""
    constraint_detail: str = ""
    constraint_icon: str = "info"
    recommended_action: str = ""
    readiness: str = ""
    material_ready: date | None = None
    staging: list[MaterialStage] = []
    ai_diagnostic: str = ""
    signoff: str = ""
    critical_shortage: bool = False
    data_complete: bool = True


# --- Supply ---------------------------------------------------------------------


class PurchaseOrder(BaseModel):
    po_number: str
    supplier_name: str
    item: str
    detail: str
    linked_order: str
    ordered_quantity: int
    received_quantity: int
    required_date: date
    expected_delivery_date: date | None
    status: str  # Open | In Transit | QC Hold | Late
    quality_status: str = ""
    note: str = ""


# --- Capacity -------------------------------------------------------------------


class Machine(BaseModel):
    machine_code: str
    name: str
    description: str
    utilization: float  # percent
    down: bool = False


class WorkCenter(BaseModel):
    code: str
    name: str
    detail: str
    group: str
    group_icon: str
    unit_label: str
    scheduled_hours: float
    capacity_hours: float
    queue_note: str
    machines: list[Machine]


# --- Scheduling -----------------------------------------------------------------


class ScheduledOperation(BaseModel):
    op_code: str
    name: str
    order_number: str
    machine_code: str
    start: datetime
    end: datetime
    note: str = ""
    critical_path: bool = False
    shortage_blocked: bool = False


class PmBlock(BaseModel):
    machine_code: str
    start: datetime
    end: datetime
    label: str
    kind: str = "pm"  # pm | downtime


class MaterialMarker(BaseModel):
    machine_code: str
    at: datetime
    label: str


class ScheduleVersion(BaseModel):
    key: str
    version_number: str
    label: str
    status: str  # Draft | Proposed | Approved | Rejected | Superseded
    is_baseline: bool
    run_number: int
    solver_status: str
    solve_seconds: float
    approved_by: str | None
    created_by: str
    change_summary: str
    operations: list[ScheduledOperation]
    blocks: list[PmBlock]
    markers: list[MaterialMarker]
    dependencies: list[tuple[str, str]]  # (from op_code, to op_code)
    late_orders: int
    total_lateness_h: int
    overtime_cost: float
    hard_violations: int
    soft_violations: int


# --- Alerts / approvals / audit -------------------------------------------------


class PlanningAlert(BaseModel):
    alert_type: str
    severity: str  # critical | warning | info
    order_number: str | None
    description: str
    recommended_action: str


class ApprovalRequest(BaseModel):
    id: str
    schedule_version: str
    run_number: int
    requested_by: str
    approver: str
    status: str  # Pending | Approved | Rejected
    compiled_at: str
    total_ops: int
    hard_violations: int
    soft_violations: int
    oee_forecast: float
    changeover_delta: str
    comments: str = ""


class AuditEntry(BaseModel):
    at: datetime
    user: str
    action: str
    entity: str
    reason: str = ""


class CurrentUser(BaseModel):
    name: str
    role: str
    can_approve: bool
