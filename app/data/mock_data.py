"""Fictional demonstration dataset (spec §13). ALL RECORDS ARE FICTIONAL.

Values are taken from the Stitch design screens and reconciled so that every page
tells the same story: SO-1005 is 3 days late because actuator PO-8821 arrives late
and Hydro Rig 1 (WC-40) is the bottleneck.
"""

from __future__ import annotations

from datetime import date, datetime

from app.models import (
    ApprovalRequest,
    BomHeader,
    BomItem,
    CurrentUser,
    ItemType,
    Machine,
    MaterialMarker,
    MaterialStage,
    OrderStatus,
    PlanningAlert,
    PmBlock,
    Priority,
    Product,
    PurchaseOrder,
    RoutingHeader,
    RoutingOperation,
    SalesOrder,
    SalesOrderLine,
    ScheduledOperation,
    ScheduleVersion,
    WorkCenter,
)

SOURCE = "mock"  # app.data.dataset: which backend produced this dataset
PLANT = "Valvetech Plant 04 - Houston TX"
PLANNING_HORIZON = (date(2025, 11, 15), date(2025, 12, 15))
GANTT_START = datetime(2025, 11, 17, 0, 0)
GANTT_DAYS = 19  # Nov 17 → Dec 5

# Used only when AUTH_MODE=disabled (development/test). Real users come from Supabase Auth (M05).
CURRENT_USER = CurrentUser(id="demo-planner", email="planner@example.com", name="Sarah Chen",
                           role="Sr. Production Planner", roles=["planner"])
APPROVER = "Marcus Vance"
APPROVER_ROLE = "Plant Operations Director"


def dt(month: int, day: int, hour: int = 6, minute: int = 0) -> datetime:
    return datetime(2025, month, day, hour, minute)


# --- Products / engineering --------------------------------------------------------

PRODUCTS: list[Product] = [
    Product(
        product_code="V-600-TBV-10", name='10-inch Class 600 Trunnion Mounted Ball Valve',
        family="Trunnion Ball", valve_type="Ball", size='10"', pressure_class="600",
        body_material="ASTM A350 LF2 Class 1", trim_material="Forged 316SS + 75µ ENP",
        connection_type="RTJ", design_standard="API 6D, 25th Edition", fire_safe="API 607 / ISO 10497",
        sour_service="NACE MR0175 / ISO 15156", hydro_test_pressure="2,225 PSI (Shell)",
        std_lead_time_days=18.5,
    ),
    Product(
        product_code="V-300-FBV-08", name='8-inch Class 300 Floating Ball Valve',
        family="Floating Ball", valve_type="Ball", size='8"', pressure_class="300",
        body_material="ASTM A216 WCB", trim_material="304SS", connection_type="RF",
        design_standard="API 6D", fire_safe="API 607", sour_service="—",
        hydro_test_pressure="1,125 PSI (Shell)", std_lead_time_days=11.0,
    ),
    Product(
        product_code="V-800-FGV-02", name='2-inch Class 800 Forged Gate Valve',
        family="Forged Gate", valve_type="Gate", size='2"', pressure_class="800",
        body_material="ASTM A105N", trim_material="13Cr / Stellite", connection_type="SW",
        design_standard="API 602", status="Pending Release",
    ),
]

BOMS: dict[str, BomHeader] = {
    "V-600-TBV-10": BomHeader(
        product_code="V-600-TBV-10", revision="Rev C", status="Approved",
        effective_from=date(2025, 11, 1), approved_by="E. Lindqvist (Chief Engineer)",
        eco_number="ECO-84920-A", plm_source="PLM Windchill v14.2",
        items=[
            BomItem(id="A0", parent=None, component_code="TBV-10-600-A", name='10" Trunnion Ball Valve Body Assembly',
                    item_type=ItemType.ASSY, quantity_per_parent=1, inventory_note="2 WIP"),
            BomItem(id="B1", parent="A0", component_code="VB-LF2-U10", name="Valve Body Upper Half",
                    material="Forged A350 LF2", item_type=ItemType.MFG, quantity_per_parent=1,
                    scrap_factor=0.02, on_hand=7),
            BomItem(id="B2", parent="A0", component_code="VB-LF2-L10", name="Valve Body Lower Half",
                    material="Forged A350 LF2", item_type=ItemType.MFG, quantity_per_parent=1,
                    scrap_factor=0.02, on_hand=7),
            BomItem(id="B3", parent="A0", component_code="TB-316-HC10", name='Trunnion Ball 10"',
                    material="316SS + Hard Chrome", item_type=ItemType.MFG, quantity_per_parent=1,
                    scrap_factor=0.035, on_hand=7),
            BomItem(id="B4", parent="A0", component_code="SR-ST6-10", name="Seat Rings with Stellite #6 Inlay",
                    item_type=ItemType.MFG, quantity_per_parent=2, scrap_factor=0.05, on_hand=10,
                    inventory_note="12 more on QC hold (PO-8840)", incoming_po="PO-8840"),
            BomItem(id="B5", parent="A0", component_code="GS-PK-010", name="Grafoil Stem Packing Set",
                    item_type=ItemType.PUR, quantity_per_parent=1, scrap_factor=0.01, on_hand=24),
            BomItem(id="B6", parent="A0", component_code="DS-IN718-4", name="Inconel 718 Disc Springs",
                    item_type=ItemType.PUR, quantity_per_parent=8, on_hand=120),
            BomItem(id="K1", parent="A0", component_code="AMK-TBV-10", name="Actuation & Mounting Kit",
                    item_type=ItemType.SUB_ASSY, quantity_per_parent=1, inventory_note="Kit phase"),
            BomItem(id="K1a", parent="K1", component_code="ACT-DA-400", name='Double Acting Pneumatic Actuator 4"',
                    item_type=ItemType.PUR, quantity_per_parent=1, on_hand=0,
                    inventory_note="PO-8821 ETA Nov 28", incoming_po="PO-8821"),
            BomItem(id="K1b", parent="K1", component_code="BRK-MNT-A36", name="Actuator Mounting Bracket",
                    material="ASTM A36", item_type=ItemType.MFG, quantity_per_parent=1,
                    scrap_factor=0.015, on_hand=8),
            BomItem(id="K1b1", parent="K1b", component_code="PL-A36-12", name="A36 Plate 12 mm",
                    item_type=ItemType.PUR, quantity_per_parent=0.25, unit_of_measure="M2",
                    scrap_factor=0.10, on_hand=5),
        ],
    ),
    "V-300-FBV-08": BomHeader(
        product_code="V-300-FBV-08", revision="Rev B", status="Approved",
        effective_from=date(2025, 6, 12), approved_by="E. Lindqvist (Chief Engineer)",
        eco_number="ECO-84133", plm_source="PLM Windchill v14.2",
        items=[
            BomItem(id="A0", parent=None, component_code="FBV-08-300-A", name='8" Floating Ball Valve Assembly',
                    item_type=ItemType.ASSY, quantity_per_parent=1),
            BomItem(id="B1", parent="A0", component_code="BD-WCB-08", name="Body Casting", material="A216 WCB",
                    item_type=ItemType.PUR, quantity_per_parent=1, scrap_factor=0.02, on_hand=16),
            BomItem(id="B2", parent="A0", component_code="BL-304-08", name='Floating Ball 8"', material="304SS",
                    item_type=ItemType.MFG, quantity_per_parent=1, scrap_factor=0.03, on_hand=14),
            BomItem(id="B3", parent="A0", component_code="ST-PTFE-08", name="RPTFE Seat Set",
                    item_type=ItemType.PUR, quantity_per_parent=2, on_hand=30),
            BomItem(id="B4", parent="A0", component_code="GS-PK-008", name="Grafoil Stem Packing Set",
                    item_type=ItemType.PUR, quantity_per_parent=1, scrap_factor=0.01, on_hand=40),
            BomItem(id="B5", parent="A0", component_code="LV-WR-08", name="Wrench Operator",
                    item_type=ItemType.PUR, quantity_per_parent=1, on_hand=20),
        ],
    ),
    "V-800-FGV-02": BomHeader(
        product_code="V-800-FGV-02", revision="Rev A", status="Draft",
        effective_from=date(2025, 12, 1), approved_by="— (awaiting engineering review)",
        eco_number="ECO-85002 (open)", plm_source="PLM Windchill v14.2",
        items=[
            BomItem(id="A0", parent=None, component_code="FGV-02-800-A", name='2" Forged Gate Valve Assembly',
                    item_type=ItemType.ASSY, quantity_per_parent=1),
            BomItem(id="B1", parent="A0", component_code="BD-A105-02", name="Forged Body", material="A105N",
                    item_type=ItemType.PUR, quantity_per_parent=1, scrap_factor=0.01, on_hand=60),
            BomItem(id="B2", parent="A0", component_code="WG-13CR-02", name="Solid Wedge", material="13Cr",
                    item_type=ItemType.MFG, quantity_per_parent=1, scrap_factor=0.02),
        ],
    ),
}

ROUTINGS: dict[str, RoutingHeader] = {
    "V-600-TBV-10": RoutingHeader(
        product_code="V-600-TBV-10", revision="RT-600-09", status="Approved",
        operations=[
            RoutingOperation(operation_number="OP-010", name="Raw Material Ultrasonic & MPI Inspection",
                             work_center="NDE Lab", duration_label="45 min / skid",
                             predecessor="Inbound Receiving Dock", note="Cap: ASNT Level II certified"),
            RoutingOperation(operation_number="OP-020", name="CNC Rough & Finish Turning",
                             work_center="Mazak Integrex e-670H", setup_minutes=90, cycle_minutes=140,
                             duration_label="Setup 90m | Cycle 140m / unit", predecessor="OP-010 Clearance",
                             min_batch=2),
            RoutingOperation(operation_number="OP-030", name="Seat Pocket Milling & Stellite Cladding",
                             work_center="Cladding Cell 1 (PTA Arc)", cycle_minutes=180,
                             duration_label="180m / unit", predecessor="OP-020 Machined Faces",
                             note="Stellite #6 Overlay Qual"),
            RoutingOperation(operation_number="OP-040", name="Ball Lapping & Seat Match-Grinding",
                             work_center="Lapping Bench C", cycle_minutes=75, duration_label="75m / unit",
                             predecessor="OP-030 Post-Weld Relief", note="Micro-finish Ra 0.2µm Spec"),
            RoutingOperation(operation_number="OP-050", name="Sub-Assembly Body & Seat Fit-Up",
                             work_center="Assembly Bay A", cycle_minutes=120, duration_label="120m / unit",
                             predecessor="OP-040 Matched Set", note="Requires Disc Spring Preload Rig"),
            RoutingOperation(operation_number="OP-060", name="API 598 Hydrostatic Shell & Seat Leak Test",
                             work_center="Hydro Rig 1 (2,250 PSI rated)", cycle_minutes=90,
                             duration_label="90m / unit", predecessor="OP-050 Completion",
                             is_mandatory_gate=True,
                             gate_detail="Requires OP-050 Completion + Grafoil Packing Lot QC Sign-off. "
                                         "Zero Bubbles Permitted (Shell: 1.5x WP | Seat: 1.1x WP)"),
            RoutingOperation(operation_number="OP-070", name="Actuator Mounting & Calibration",
                             work_center="Instrumentation Station", cycle_minutes=60, duration_label="60m / unit",
                             predecessor="OP-060 Hydro Pass + PO-8821 receipt", note="Limit switch trip verification"),
            RoutingOperation(operation_number="OP-080", name="High-Build Epoxy Coating & Curing",
                             work_center="Epoxy Paint Booth 2", cycle_minutes=240, duration_label="240m (Batch Oven)",
                             predecessor="OP-070 Calibration", min_batch=4, note="Min Batch: 4 valves / bake"),
            RoutingOperation(operation_number="OP-090", name="Final Inspection, Tagging & ASME Stamping",
                             work_center="QC Final Shipping Prep", cycle_minutes=45, duration_label="45m / unit",
                             predecessor="OP-080 Dry-Film Thickness Pass", note="ASME B16.34 Nameplate Attached"),
        ],
    ),
    "V-300-FBV-08": RoutingHeader(
        product_code="V-300-FBV-08", revision="RT-02-std", status="Approved",
        operations=[
            RoutingOperation(operation_number="OP-010", name="Incoming Casting Inspection", work_center="NDE Lab",
                             duration_label="20 min / unit", predecessor="Receiving"),
            RoutingOperation(operation_number="OP-035", name="Bore & Face Machining", work_center="Okuma Multus U4000",
                             setup_minutes=60, cycle_minutes=85, duration_label="Setup 60m | Cycle 85m / unit",
                             predecessor="OP-010"),
            RoutingOperation(operation_number="OP-065", name="Ball & Seat Lapping", work_center="Lapping Bench C",
                             cycle_minutes=40, duration_label="40m / unit", predecessor="OP-035"),
            RoutingOperation(operation_number="OP-060", name="API 598 Hydrostatic Test", work_center="Hydro Rig 1",
                             cycle_minutes=45, duration_label="45m / unit", predecessor="OP-065",
                             is_mandatory_gate=True, gate_detail="Shell 1.5x WP, Seat 1.1x WP. Zero visible leakage."),
            RoutingOperation(operation_number="OP-082", name="Final Assembly & Operator Fit", work_center="Valve Bay B",
                             cycle_minutes=50, duration_label="50m / unit", predecessor="OP-060"),
            RoutingOperation(operation_number="OP-090", name="Final Inspection & Packing", work_center="QC Final",
                             cycle_minutes=20, duration_label="20m / unit", predecessor="OP-082"),
        ],
    ),
}

# --- Capacity ---------------------------------------------------------------------

WORK_CENTERS: list[WorkCenter] = [
    WorkCenter(code="WC-10", name="CNC Horizontal Machining Center", detail="Mazak / Okuma 5-Axis",
               group="CNC Machining", group_icon="precision_manufacturing", unit_label="2 Units",
               scheduled_hours=187.2, capacity_hours=240, queue_note="Queue: 3 Pallets • Avg Setup: 42m",
               machines=[
                   Machine(machine_code="MC-010", name="Mazak Integrex e-670H", description="Heavy Milling / 5-Axis",
                           utilization=94),
                   Machine(machine_code="MC-014", name="Okuma Multus U4000", description="Seat & Cavity Turning",
                           utilization=78),
               ]),
    WorkCenter(code="WC-14", name="Conventional Heavy Turning", detail='VTL Bullard 64"',
               group="Heavy Turning", group_icon="settings", unit_label="1 Unit",
               scheduled_hours=148.8, capacity_hours=240, queue_note="Queue: 1 Flange casting • Tooling OK",
               machines=[
                   Machine(machine_code="VT-001", name='VTL Bullard 64"', description="Large Bore Facing",
                           utilization=62),
               ]),
    WorkCenter(code="WC-22", name="Automated TIG Cladding & Overlay", detail="Inconel 625 Hot Wire",
               group="Welding & Cladding", group_icon="local_fire_department", unit_label="2 Units",
               scheduled_hours=211.2, capacity_hours=240, queue_note="Queue: 4 Seat pockets • Shield gas: 98%",
               machines=[
                   Machine(machine_code="WL-002", name="Cladding Cell 1 (Inconel)", description="Weld Overlay Station",
                           utilization=88),
                   Machine(machine_code="WL-005", name="Automated Submerged Arc", description="Flange Butt Welder",
                           utilization=65),
               ]),
    WorkCenter(code="WC-30", name="Precision Valve Assembly & Trim Fit", detail="Bays 1-3 Cleanroom",
               group="Sub-Assembly", group_icon="handyman", unit_label="2 Benches",
               scheduled_hours=170.4, capacity_hours=240, queue_note="3 Crews manned • Torque cal: Active",
               machines=[
                   Machine(machine_code="SA-008", name="Stem & Ball Lapping Bench", description="Optical Flat Tolerancing",
                           utilization=72),
                   Machine(machine_code="SA-012", name="Packing Assembly Station", description="Graphite Seal Insert",
                           utilization=81),
               ]),
    WorkCenter(code="WC-35", name="Final Valve Assembly", detail="30-Ton Crane Bays",
               group="Final Assembly", group_icon="domain", unit_label="2 Bays",
               scheduled_hours=196.8, capacity_hours=240, queue_note="Bay A crane booked 06:00–14:00",
               machines=[
                   Machine(machine_code="FA-001", name="Heavy Valve Bay A", description="30-Ton Crane Assist",
                           utilization=96),
                   Machine(machine_code="FA-002", name="Valve Bay B (Medium Bore)", description="Standard Frame Assembly",
                           utilization=60),
               ]),
    WorkCenter(code="WC-40", name="Hydrostatic & Cryo Pressure Cell", detail="Testing & NDE",
               group="Testing & NDE", group_icon="science", unit_label="3 Cells",
               scheduled_hours=230.4, capacity_hours=240, queue_note="Critical Backlog: 7 valves • Wait Time: 14.2h",
               machines=[
                   Machine(machine_code="TE-001", name="Hydrostatic Rig 1 (HP)", description="22,500 PSI Enclosure",
                           utilization=100, down=True),
                   Machine(machine_code="TE-004", name="Gas Helium Cryo Cell", description="-196°C Sniffer Chamber",
                           utilization=50),
                   Machine(machine_code="ND-001", name="Radiographic Bunker", description="X-Ray / Gamma RT Vault",
                           utilization=40),
               ]),
    WorkCenter(code="WC-50", name="Industrial Blasting, Primer & Coating", detail="Enclosed Epoxy Booth",
               group="Surface Finishing", group_icon="format_paint", unit_label="2 Lines",
               scheduled_hours=129.6, capacity_hours=240, queue_note="Cure Oven: Running Nominally",
               machines=[
                   Machine(machine_code="SF-001", name="Blast Booth (Garnet)", description="Sa 2.5 Profiling",
                           utilization=55),
                   Machine(machine_code="SF-003", name="Epoxy Paint Line", description="Marine C5M Curing Oven",
                           utilization=70),
               ]),
]

# --- Sales orders ----------------------------------------------------------------

_STAGING_1005 = [
    MaterialStage(name="Castings (A350 LF2 Bodies)", staged=6, required=6, note="100% Staged"),
    MaterialStage(name="Internals & Ball Core (316SS)", staged=6, required=6, note="100% Inspected"),
    MaterialStage(name="Seals, Seat Rings & RTJ Gaskets", staged=10, required=13,
                  note="10/13 — 12 on QC hold (PO-8840)", blocked_by="PO-8840"),
    MaterialStage(name="Pneumatic Actuators (Rotork GT-40)", staged=0, required=6,
                  note="0% In Plant", blocked_by="PO-8821"),
]

SALES_ORDERS: list[SalesOrder] = [
    SalesOrder(
        order_number="SO-1005", customer_name="Shell Offshore GOM", customer_reference="PO-2025-9981",
        customer_profile="Tier-1 Energy / Strict Contractual Late Penalties", customer_otd=99.1,
        product_code="V-600-TBV-10", spec_title='10" Cl 600 Trunnion Ball', spec_detail="A350 LF2 / 316SS Trim / RTJ",
        tag="XV-2101-A/B", quantity=6,
        lines=[
            SalesOrderLine(line_number=1, description="Production Master Runs", quantity=4,
                           tags="Tag XV-2101-A through D", note="Routing Step 03/09"),
            SalesOrderLine(line_number=2, description="Subsea Spares & Interchange", quantity=2,
                           tags="Tag XV-2101-SP1 / SP2", note="Held for Batch Pack"),
        ],
        bom_revision="Rev C", bom_status="Approved", routing_revision="RT-600-09", work_centers="WC-CNC / WC-ASMY",
        order_date=date(2025, 10, 6), requested_delivery_date=date(2025, 11, 30),
        projected_completion=date(2025, 12, 3), priority=Priority.CRITICAL, status=OrderStatus.PLANNING,
        value_usd=486_000, limiting_constraint="Actuator Shortage + Hydro Rig 1 Overload",
        constraint_detail="PO-8821 ETA Nov 28 vs need Nov 24", constraint_icon="call_split",
        recommended_action="Simulate Expedite + Sat OT", readiness="Actuator Shortage",
        material_ready=date(2025, 11, 28), staging=_STAGING_1005, critical_shortage=True,
        ai_diagnostic="Actuator delay (PO-8821) blocks OP-070 mounting on Bay A scheduled for Nov 24, and Hydro Rig 1 "
                      "has no free slot until Dec 1. Expediting PO-8821 to Nov 25 plus one Saturday overtime shift "
                      "on Hydro Rig 1 finishes Nov 29, one day ahead of the requested date.",
        signoff="Marcus Vance, PE on Oct 14, 2025 (ECN-0892 Validated)",
    ),
    SalesOrder(
        order_number="SO-1008", customer_name="Chevron Pascagoula", customer_reference="PO-CHV-4410",
        customer_profile="Tier-1 Refining / Standard Terms", customer_otd=96.4,
        product_code="V-300-FBV-08", spec_title='8" Cl 300 Floating Ball', spec_detail="WCB Body / 304SS Trim / RF",
        tag="FB-8300-C", quantity=12,
        lines=[SalesOrderLine(line_number=1, description="Refinery Isolation Valves", quantity=12,
                              tags="Tag FB-8300-C01..C12", note="Hydro Nov 26")],
        bom_revision="Rev B", bom_status="Approved", routing_revision="RT-02-std", work_centers="WC-MILL / WC-PRESS",
        order_date=date(2025, 10, 14), requested_delivery_date=date(2025, 12, 8),
        projected_completion=date(2025, 12, 6), priority=Priority.NORMAL, status=OrderStatus.PLANNED,
        value_usd=168_000, limiting_constraint="None", constraint_detail="100% Cleared",
        constraint_icon="task_alt", recommended_action="—", readiness="100% Cleared",
        material_ready=date(2025, 11, 14),
        staging=[MaterialStage(name="Body Castings (WCB)", staged=12, required=12, note="100% Staged"),
                 MaterialStage(name="Balls & Seats", staged=12, required=12, note="100% In Kit")],
        ai_diagnostic="On schedule with 2 days of slack. Hydro slot Nov 26–27 confirmed on Rig 1.",
        signoff="E. Lindqvist on Jun 12, 2025",
    ),
    SalesOrder(
        order_number="SO-1012", customer_name="ExxonMobil Baytown", customer_reference="PO-XOM-7734",
        customer_profile="Tier-1 Petrochemical / LD Clause 0.5%/day", customer_otd=97.8,
        product_code="V-600-TBV-10", spec_title='14" Cl 900 Metal-Seated', spec_detail="F22 Alloy Body / Stellite Trim",
        tag="ESDV-0091-HT", quantity=4,
        lines=[SalesOrderLine(line_number=1, description="Emergency Shutdown Valves", quantity=4,
                              tags="Tag ESDV-0091-HT..HT4", note="VTL queue")],
        bom_revision="Rev D (ECN Active)", bom_status="Approved", routing_revision="RT-09-Heavy",
        work_centers="WC-VTL-01 / WC-TEST", order_date=date(2025, 9, 30),
        requested_delivery_date=date(2025, 12, 15), projected_completion=date(2025, 12, 20),
        priority=Priority.HIGH, status=OrderStatus.PLANNING, value_usd=392_000,
        limiting_constraint="VTL Capacity Overload", constraint_detail="WC-14 single machine, 5d queue",
        constraint_icon="settings", recommended_action="Offload facing to MC-010", readiness="Machine Conflict",
        material_ready=date(2025, 11, 17),
        staging=[MaterialStage(name="F22 Forgings", staged=4, required=4, note="100% Staged"),
                 MaterialStage(name="Inconel 718 Flange Studs", staged=0, required=96, note="In transit (PO-8815)",
                               blocked_by="PO-8815")],
        ai_diagnostic="The VTL (WC-14) is the only qualified resource for 14\" body facing. Moving OP-015 roughing "
                      "to MC-010 is qualified per routing alternates and would recover ~2 days.",
        signoff="E. Lindqvist on Sep 22, 2025",
    ),
    SalesOrder(
        order_number="SO-1014", customer_name="BP Whiting Ref", customer_reference="PO-BP-8802",
        customer_profile="Tier-2 Refining / Standard Terms", customer_otd=95.0,
        product_code="V-300-FBV-08", spec_title='6" Cl 150 Dual Plate Check', spec_detail="CF8M Stainless / Inconel Spring",
        tag="N-NRV-04", quantity=18,
        lines=[SalesOrderLine(line_number=1, description="Check valves — Unit 4", quantity=8, tags="N-NRV-04-01..08", note="In paint"),
               SalesOrderLine(line_number=2, description="Check valves — Unit 5", quantity=6, tags="N-NRV-05-01..06", note="Machining"),
               SalesOrderLine(line_number=3, description="Spares", quantity=4, tags="N-NRV-SP", note="Kitted")],
        bom_revision="Rev A (Frozen)", bom_status="Approved", routing_revision="RT-01-Fast", work_centers="WC-CNC / WC-INSP",
        order_date=date(2025, 10, 20), requested_delivery_date=date(2025, 12, 22),
        projected_completion=date(2025, 12, 22), priority=Priority.NORMAL, status=OrderStatus.IN_PRODUCTION,
        value_usd=97_000, limiting_constraint="0 Days Slack", constraint_detail="Nominal pace, NDT cell release",
        constraint_icon="schedule", recommended_action="Monitor", readiness="Material 100% Staged",
        material_ready=date(2025, 11, 10),
        staging=[MaterialStage(name="CF8M Bodies", staged=18, required=18, note="100% Staged")],
        ai_diagnostic="Zero slack: any slip at SF-003 paint line moves delivery. No current blockers.",
        signoff="E. Lindqvist on Aug 02, 2025",
    ),
    SalesOrder(
        order_number="SO-1015", customer_name="Woodside Energy", customer_reference="PO-WDS-3307",
        customer_profile="Tier-1 LNG / Standard Terms", customer_otd=98.2,
        product_code="V-300-FBV-08", spec_title='4" Dual Plate Check', spec_detail="CF8M / Inconel X-750 Spring",
        tag="LNG-DPC-04", quantity=10,
        lines=[SalesOrderLine(line_number=1, description="LNG train check valves", quantity=10, tags="LNG-DPC-04-01..10", note="Coating")],
        bom_revision="Rev B", bom_status="Approved", routing_revision="RT-01-Fast", work_centers="WC-ASMY / WC-PAINT",
        order_date=date(2025, 10, 2), requested_delivery_date=date(2025, 12, 12),
        projected_completion=date(2025, 12, 8), priority=Priority.NORMAL, status=OrderStatus.IN_PRODUCTION,
        value_usd=74_000, limiting_constraint="None", constraint_detail="Coating Nov 25–26",
        constraint_icon="task_alt", recommended_action="—", readiness="100% Cleared",
        material_ready=date(2025, 11, 12),
        staging=[MaterialStage(name="Plates & Springs", staged=10, required=10, note="100% In Kit")],
        ai_diagnostic="4 days of slack after C5M coating; no constraints detected.",
        signoff="E. Lindqvist on Jul 18, 2025",
    ),
    SalesOrder(
        order_number="SO-1017", customer_name="ConocoPhillips AK", customer_reference="PO-COP-1029",
        customer_profile="Tier-1 Upstream / Arctic Spec", customer_otd=98.9,
        product_code="V-600-TBV-10", spec_title='12" Cl 1500 Cryo Ball Valve', spec_detail="316L Ext-Bonnet / Kel-F / RTJ",
        tag="CRYO-VL-02", quantity=2,
        lines=[SalesOrderLine(line_number=1, description="Cryogenic isolation", quantity=2, tags="CRYO-VL-02-A/B", note="Castings inspection")],
        bom_revision="Rev B", bom_status="Approved", routing_revision="RT-Cryo-HighSpec", work_centers="WC-CRYO-TEST",
        order_date=date(2025, 10, 28), requested_delivery_date=date(2026, 1, 12),
        projected_completion=date(2026, 1, 9), priority=Priority.NORMAL, status=OrderStatus.CONFIRMED,
        value_usd=238_000, limiting_constraint="Casting Cert Inspection", constraint_detail="Secure path",
        constraint_icon="verified", recommended_action="—", readiness="Castings Inspection",
        material_ready=date(2025, 12, 1),
        staging=[MaterialStage(name="316L Castings", staged=2, required=2, note="Cert review")],
        ai_diagnostic="3 days of buffer. Cryo test cell (TE-004) has 50% load in the window.",
        signoff="E. Lindqvist on Oct 21, 2025",
    ),
    SalesOrder(
        order_number="SO-1019", customer_name="ExxonMobil Beaumont", customer_reference="PO-XOM-7790",
        customer_profile="Tier-1 Refining / LD Clause", customer_otd=97.1,
        product_code="V-300-FBV-08", spec_title='4" Control Globe Valve', spec_detail="WCC / Monel Plug Trim",
        tag="FV-4410", quantity=8,
        lines=[SalesOrderLine(line_number=1, description="Control valves", quantity=8, tags="FV-4410-01..08", note="Hardfacing")],
        bom_revision="Rev C", bom_status="Approved", routing_revision="RT-05-Globe", work_centers="WC-WELD / WC-ASMY",
        order_date=date(2025, 10, 9), requested_delivery_date=date(2025, 12, 4),
        projected_completion=date(2025, 12, 4), priority=Priority.HIGH, status=OrderStatus.PLANNING,
        value_usd=88_000, limiting_constraint="TIG Hardfacing Schedule", constraint_detail="Robot #2 recalibration",
        constraint_icon="precision_manufacturing", recommended_action="Pre-stage Weld Cell",
        readiness="Buffer 1.5h", material_ready=date(2025, 11, 18),
        staging=[MaterialStage(name="Plug & Cage Blanks", staged=8, required=8, note="100% Staged")],
        ai_diagnostic="Delivery holds with only 1.5h margin; depends on WL-002 cladding finishing Nov 22.",
        signoff="E. Lindqvist on Sep 30, 2025",
    ),
    SalesOrder(
        order_number="SO-1020", customer_name="Chevron Deepwater", customer_reference="PO-CHV-4471",
        customer_profile="Tier-1 Energy / Standard Terms", customer_otd=96.4,
        product_code="V-300-FBV-08", spec_title='6" Cryogenic Gate Valve', spec_detail="CF8M / Extended Bonnet",
        tag="CG-0611", quantity=5,
        lines=[SalesOrderLine(line_number=1, description="Cryo gate valves", quantity=5, tags="CG-0611-01..05", note="Machining")],
        bom_revision="Rev A", bom_status="Approved", routing_revision="RT-Cryo-Std", work_centers="WC-CNC / WC-CRYO",
        order_date=date(2025, 10, 30), requested_delivery_date=date(2025, 12, 18),
        projected_completion=date(2025, 12, 16), priority=Priority.NORMAL, status=OrderStatus.CONFIRMED,
        value_usd=121_000, limiting_constraint="None", constraint_detail="Cryo test Nov 28",
        constraint_icon="task_alt", recommended_action="—", readiness="100% Cleared",
        material_ready=date(2025, 11, 20),
        staging=[MaterialStage(name="CF8M Castings", staged=5, required=5, note="100% Staged")],
        ai_diagnostic="2 days of slack; cryo test scheduled Nov 28 on TE-004.",
        signoff="E. Lindqvist on Oct 25, 2025",
    ),
    SalesOrder(
        order_number="SO-1021", customer_name="Shell Deer Park", customer_reference="PO-SDP-0042",
        customer_profile="Tier-1 Refining / Standard Terms", customer_otd=97.5,
        product_code="V-800-FGV-02", spec_title='2" Cl 800 Forged Gate', spec_detail="A105N / 13Cr-Stellite Trim / SW",
        tag="HV-0200", quantity=40,
        lines=[SalesOrderLine(line_number=1, description="Small-bore isolation", quantity=40, tags="HV-0200-01..40", note="Needs data")],
        bom_revision="Rev A (Draft)", bom_status="Draft", routing_revision="— Missing", work_centers="—",
        order_date=date(2025, 11, 10), requested_delivery_date=date(2026, 1, 5),
        projected_completion=None, priority=Priority.NORMAL, status=OrderStatus.DRAFT,
        value_usd=46_000, limiting_constraint="Needs Data", constraint_detail="No approved BOM or routing",
        constraint_icon="help", recommended_action="Request ECO-85002 release", readiness="Needs Data",
        data_complete=False,
        ai_diagnostic="Cannot plan: BOM V-800-FGV-02 is Draft (ECO-85002 open) and no routing exists. "
                      "This order is not production-ready (acceptance test 8).",
        signoff="— pending",
    ),
    SalesOrder(
        order_number="SO-1022", customer_name="Equinor Mongstad", customer_reference="PO-EQN-5512",
        customer_profile="Tier-1 Energy / NORSOK", customer_otd=99.4,
        product_code="V-600-TBV-10", spec_title='10" Cl 600 Trunnion Ball', spec_detail="A350 LF2 / 316SS Trim / RTJ",
        tag="XV-3300", quantity=3,
        lines=[SalesOrderLine(line_number=1, description="Isolation valves", quantity=3, tags="XV-3300-A..C", note="Queued")],
        bom_revision="Rev C", bom_status="Approved", routing_revision="RT-600-09", work_centers="WC-CNC / WC-ASMY",
        order_date=date(2025, 11, 3), requested_delivery_date=date(2025, 12, 30),
        projected_completion=date(2025, 12, 23), priority=Priority.LOW, status=OrderStatus.CONFIRMED,
        value_usd=243_000, limiting_constraint="Shares PO-8821 actuators", constraint_detail="Allocated after SO-1005",
        constraint_icon="inventory_2", recommended_action="—", readiness="Allocation pending",
        material_ready=date(2025, 12, 8),
        staging=[MaterialStage(name="Pneumatic Actuators", staged=0, required=3, note="Allocated after SO-1005",
                               blocked_by="PO-8821")],
        ai_diagnostic="Competes with SO-1005 for PO-8821 actuators; allocation gives SO-1005 priority "
                      "(Critical). 7 days of buffer remain (acceptance test 10).",
        signoff="E. Lindqvist on Oct 30, 2025",
    ),
]

# --- Purchasing ---------------------------------------------------------------------

PURCHASE_ORDERS: list[PurchaseOrder] = [
    PurchaseOrder(po_number="PO-8821", supplier_name="Rotork UK Ltd", item='Rotork Pneumatic 4"',
                  detail="Part #ACT-PN-04-SS", linked_order="SO-1005", ordered_quantity=9, received_quantity=0,
                  required_date=date(2025, 11, 24), expected_delivery_date=date(2025, 11, 28), status="Late",
                  note="Air freight option: Nov 25 (+$650)"),
    PurchaseOrder(po_number="PO-8840", supplier_name="Deloro Stellite", item="Stellite 6 Seat Rings",
                  detail="Heat Lot #HT-9921-A", linked_order="SO-1005", ordered_quantity=12, received_quantity=12,
                  required_date=date(2025, 11, 19), expected_delivery_date=date(2025, 11, 20), status="QC Hold",
                  quality_status="PMI Audit Pending", note="Holding: Dock Receiving #2"),
    PurchaseOrder(po_number="PO-8815", supplier_name="Bolt Alloys Inc", item="Inconel 718 Flange Studs",
                  detail="M24 x 180mm Heavy Hex Bolting Pack (Qty: 96)", linked_order="SO-1012",
                  ordered_quantity=96, received_quantity=0, required_date=date(2025, 11, 26),
                  expected_delivery_date=date(2025, 11, 25), status="In Transit", note="Carrier ETA 09:00"),
    PurchaseOrder(po_number="PO-8833", supplier_name="GrafTech Seals", item="Grafoil Packing Sets",
                  detail="GS-PK-008 / GS-PK-010", linked_order="SO-1008", ordered_quantity=60, received_quantity=60,
                  required_date=date(2025, 11, 18), expected_delivery_date=date(2025, 11, 12), status="Received",
                  quality_status="Released"),
]

# --- Schedules ------------------------------------------------------------------------


def _op(code, name, order, machine, start, end, note="", cp=False, short=False) -> ScheduledOperation:
    return ScheduledOperation(op_code=code, name=name, order_number=order, machine_code=machine, start=start,
                              end=end, note=note, critical_path=cp, shortage_blocked=short)


_COMMON_OPS = [
    # SO-1005 upstream chain (identical in both versions)
    _op("OP-020", "Body Rough Turn", "SO-1005", "MC-010", dt(11, 17), dt(11, 18, 18), "36h • Setup Cleared", cp=True),
    _op("OP-030", "Inconel 625 Seat Clad", "SO-1005", "WL-002", dt(11, 19), dt(11, 20, 18), "PTA Arc", cp=True),
    _op("OP-040", "Seat Pocket Lapping", "SO-1005", "SA-008", dt(11, 21), dt(11, 21, 22), "Ra 0.2µm", cp=True),
    _op("OP-050", "Body & Seat Fit-Up", "SO-1005", "FA-001", dt(11, 24), dt(11, 25, 18), "Disc spring preload", cp=True),
    # SO-1008
    _op("OP-035", "Globe Bore Machining", "SO-1008", "MC-014", dt(11, 18), dt(11, 19, 14)),
    _op("OP-065", "Needle Seat Lapping", "SO-1008", "SA-008", dt(11, 24), dt(11, 25, 10)),
    _op("OP-060", "API 598 Hydro", "SO-1008", "TE-001", dt(11, 26), dt(11, 27, 14), "12 valves"),
    _op("OP-082", "Final Assembly", "SO-1008", "FA-002", dt(11, 27, 14), dt(11, 28, 22)),
    # SO-1012
    _op("OP-015", "Axial Body Facing", "SO-1012", "VT-001", dt(11, 17), dt(11, 21, 18), "48h • In-Spec Inconel", cp=True),
    _op("OP-050", "Flange Circum-Weld", "SO-1012", "WL-005", dt(11, 24), dt(11, 25, 22), "40h • Tandem Wire", cp=True),
    _op("OP-100", "100% Volumetric RT", "SO-1012", "ND-001", dt(11, 26, 6), dt(11, 27, 6), "Studs PO-8815", cp=True),
    _op("OP-060", "API 598 Hydro", "SO-1012", "TE-001", dt(11, 27, 14), dt(11, 28, 22), "4 valves", cp=True),
    # SO-1015
    _op("OP-075", "Spring & Hinge Pin", "SO-1015", "SA-012", dt(11, 18), dt(11, 19, 14)),
    _op("OP-098", "Sa 2.5 Blast", "SO-1015", "SF-001", dt(11, 24), dt(11, 24, 18), "Garnet"),
    _op("OP-100", "C5M 3-Coat", "SO-1015", "SF-003", dt(11, 25), dt(11, 26, 18), "Force cure"),
    # SO-1019
    _op("OP-025", "Plug & Cage Machining", "SO-1019", "MC-010", dt(11, 19), dt(11, 21, 14)),
    _op("OP-045", "TIG Hardfacing", "SO-1019", "WL-002", dt(11, 21), dt(11, 21, 22), "Robot #2"),
    _op("OP-055", "Globe Assembly", "SO-1019", "FA-002", dt(11, 24), dt(11, 26, 14)),
    _op("OP-062", "Helium Leak Test", "SO-1019", "TE-004", dt(12, 1), dt(12, 2, 14)),
    # SO-1014
    _op("OP-030", "Disc Machining", "SO-1014", "MC-014", dt(11, 24), dt(11, 26, 18)),
    _op("OP-080", "Epoxy Coat", "SO-1014", "SF-003", dt(12, 1), dt(12, 2, 10)),
    # SO-1020
    _op("OP-020", "Body Turning", "SO-1020", "MC-010", dt(11, 24), dt(11, 26, 18)),
    _op("OP-060", "Cryo Test", "SO-1020", "TE-004", dt(11, 28), dt(11, 28, 18), "-196°C"),
]

_BASELINE_TAIL = [
    _op("OP-060", "Hydro Shell & Seat", "SO-1005", "TE-001", dt(12, 1), dt(12, 1, 18), "Rig free Dec 1", cp=True),
    _op("OP-070", "Actuator Mount", "SO-1005", "FA-001", dt(12, 2), dt(12, 2, 14), "Needs PO-8821", cp=True, short=True),
    _op("OP-080", "Epoxy Coat & Cure", "SO-1005", "SF-003", dt(12, 2, 14), dt(12, 3, 10), "Batch oven", cp=True),
    _op("OP-090", "Final Insp. & Pack", "SO-1005", "SA-012", dt(12, 3, 10), dt(12, 3, 18), "Ships Dec 3", cp=True),
]

_SCENARIO_TAIL = [
    _op("OP-060", "Hydro Shell & Seat", "SO-1005", "TE-001", dt(11, 29), dt(11, 29, 12), "Sat OT shift", cp=True),
    _op("OP-070", "Actuator Mount", "SO-1005", "FA-001", dt(11, 29, 12), dt(11, 29, 16), "PO-8821 exp. Nov 25", cp=True),
    _op("OP-080", "Epoxy Coat & Cure", "SO-1005", "SF-003", dt(11, 29, 16), dt(11, 29, 21), "Force cure", cp=True),
    _op("OP-090", "Final Insp. & Pack", "SO-1005", "SA-012", dt(11, 29, 21), dt(11, 29, 23, 30), "Ships Nov 29", cp=True),
]

_BLOCKS = [
    PmBlock(machine_code="MC-010", start=dt(11, 21, 14), end=dt(11, 21, 22), label="Spindle PM"),
    PmBlock(machine_code="TE-001", start=dt(11, 24), end=dt(11, 25, 18), label="Pump overhaul (confirmed)",
            kind="downtime"),
    PmBlock(machine_code="SF-001", start=dt(12, 2), end=dt(12, 2, 12), label="Media change"),
]

_DEPS_1005 = [("SO-1005:OP-020", "SO-1005:OP-030"), ("SO-1005:OP-030", "SO-1005:OP-040"),
              ("SO-1005:OP-040", "SO-1005:OP-050"), ("SO-1005:OP-050", "SO-1005:OP-060"),
              ("SO-1005:OP-060", "SO-1005:OP-070"), ("SO-1005:OP-070", "SO-1005:OP-080"),
              ("SO-1005:OP-080", "SO-1005:OP-090"),
              ("SO-1012:OP-015", "SO-1012:OP-050"), ("SO-1012:OP-050", "SO-1012:OP-100"),
              ("SO-1012:OP-100", "SO-1012:OP-060")]

SCHEDULE_VERSIONS: dict[str, ScheduleVersion] = {
    "v4.2": ScheduleVersion(
        key="v4.2", version_number="4.2", label="Version 4.2 [Baseline Active]", status="Approved",
        is_baseline=True, run_number=1038, solver_status="FEASIBLE_WITH_LATENESS", solve_seconds=3.9,
        approved_by=APPROVER, created_by="Sarah Chen", change_summary="Weekly baseline",
        operations=_COMMON_OPS + _BASELINE_TAIL, blocks=_BLOCKS,
        markers=[MaterialMarker(machine_code="FA-001", at=dt(11, 28, 8), label="PO-8821 MATL")],
        dependencies=_DEPS_1005, late_orders=2, total_lateness_h=192, overtime_cost=0,
        hard_violations=0, soft_violations=2,
    ),
    "v4.3": ScheduleVersion(
        key="v4.3", version_number="4.3", label="Version 4.3 [Scenario C — Candidate]", status="Proposed",
        is_baseline=False, run_number=1042, solver_status="FEASIBLE_WITH_LATENESS", solve_seconds=4.2,
        approved_by=None, created_by="Sarah Chen",
        change_summary="Expedite PO-8821 to Nov 25 (air freight) + Saturday OT shift on Hydro Rig 1",
        operations=_COMMON_OPS + _SCENARIO_TAIL,
        blocks=_BLOCKS + [PmBlock(machine_code="TE-001", start=dt(11, 29), end=dt(11, 29, 14),
                                  label="Approved OT", kind="ot")],
        markers=[MaterialMarker(machine_code="FA-001", at=dt(11, 25, 8), label="PO-8821 MATL")],
        dependencies=_DEPS_1005, late_orders=1, total_lateness_h=120, overtime_cost=1490,
        hard_violations=0, soft_violations=1,
    ),
}

# Order colours on the Gantt (cobalt family + accent per order)
ORDER_COLORS: dict[str, str] = {
    "SO-1005": "bg-primary text-on-primary",
    "SO-1008": "bg-primary-fixed text-on-primary-fixed",
    "SO-1012": "bg-tertiary-container text-on-tertiary-container",
    "SO-1014": "bg-secondary-container text-on-secondary-container",
    "SO-1015": "bg-cobalt text-white",
    "SO-1019": "bg-surface-bright text-on-surface",
    "SO-1020": "bg-tertiary-fixed text-on-tertiary-fixed",
}

# --- Approvals / AI insights -----------------------------------------------------------

APPROVAL_REQUESTS: list[ApprovalRequest] = [
    ApprovalRequest(id="APR-1042", schedule_version="v4.3", run_number=1042, requested_by="Sarah Chen",
                    approver=APPROVER, status="Pending", compiled_at="Nov 20, 14:12 CST", total_ops=142,
                    hard_violations=0, soft_violations=1, oee_forecast=88.4, changeover_delta="-18% vs v4.2"),
]

COPILOT_INSIGHTS = [
    {
        "kind": "Schedule Recovery Recommendation",
        "age": "12m ago",
        "html": 'Enable <b>1 additional overtime shift</b> on <b>Hydro Rig 1</b> on <b>Nov 29</b> and expedite '
                '<b>PO-8821</b> to absorb the testing backlog and recover <a class="text-primary" '
                'href="/orders?selected=SO-1005">SO-1005 (Shell Offshore)</a> delivery commitment.',
        "facts": [("Impact", "+8h capacity"), ("Lateness avoided", "-72h")],
        "actions": [("Simulate Impact", "/assistant"), ("Review in Workbench", "/workbench?version=v4.3")],
    },
    {
        "kind": "Scenario Synthesis",
        "age": "41m ago",
        "html": 'Draft Scenario <b>v4.3 (Expedited PO-8821 Air Freight + Sat OT)</b> is compiled and ready for '
                'operational review. Late orders drop from <b>2 → 1</b>.',
        "facts": [],
        "actions": [("Review in Workbench", "/workbench?version=v4.3"), ("Launch What-If", "/assistant")],
    },
]

ASSISTANT_QUICK_PROMPTS = [
    "Why is SO-1012 late?",
    "Which machine is the current bottleneck?",
    "What changed between v4.2 and v4.3?",
    "Which orders are most at risk?",
]


# --- Lists used by the Inventory & Purchasing and BOM revision screens (M06) -----------------------

INVENTORY = [
    {"id": None, "product_code": item.component_code, "product_name": item.name, "warehouse_code": "WH-MAIN",
     "lot_number": "", "on_hand": item.on_hand, "reserved": item.reserved, "quality_hold": item.quality_hold,
     "net_available": max(item.on_hand - item.reserved - item.quality_hold, 0)}
    for bom in BOMS.values() for item in bom.items if item.on_hand is not None
]

PO_LINES = [
    {"id": None, "po_number": po.po_number, "line_number": 1, "supplier_name": po.supplier_name,
     "product_code": po.detail, "product_name": po.item, "ordered": po.ordered_quantity, "received": po.received_quantity,
     "open": po.ordered_quantity - po.received_quantity, "expected": po.expected_delivery_date, "status": po.status,
     "quality_status": po.quality_status or "Not Inspected", "warehouse_code": "WH-DOCK", "notes": po.note}
    for po in PURCHASE_ORDERS
]

BOM_REVISIONS = {
    code: [{"id": None, "revision": b.revision, "status": b.status, "effective_from": b.effective_from,
            "effective_to": None, "eco_number": b.eco_number, "approved_by": b.approved_by}]
    for code, b in BOMS.items()
}
