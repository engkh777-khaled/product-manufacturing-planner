"""Build the app's dataset from `public.planning_snapshot()` (M06).

The snapshot is fetched with the signed-in user's token, so Row Level Security decides what it contains.
It is converted into the same models as `mock_data`, which keeps every page and repository function identical
for both backends. Display fields the database does not store (e.g. "limiting constraint") are derived here
from planning alerts, BOMs and schedules — never invented.
"""

from __future__ import annotations

import math
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx

from app import planning
from planning.mrp import from_snapshot as mrp_input
from planning.mrp import run_mrp
from app.data import mock_data
from app.models import (
    ApprovalRequest,
    BomHeader,
    BomItem,
    ItemType,
    Machine,
    MaterialMarker,
    MaterialStage,
    OrderStatus,
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

PLANT_TZ = ZoneInfo("America/Chicago")
CACHE_TTL_SECONDS = 30

ALERT_LABELS = {
    "material_shortage": ("Material Shortage", "inventory_2"),
    "late_supplier_receipt": ("Late Supplier Receipt", "local_shipping"),
    "quality_hold": ("Material on QC Hold", "gpp_maybe"),
    "capacity_overload": ("Capacity Overload", "speed"),
    "missing_bom": ("Needs Data", "help"),
    "missing_routing": ("Needs Data", "help"),
    "delivery_date_at_risk": ("Delivery Date at Risk", "event_busy"),
    "machine_downtime_conflict": ("Machine Downtime Conflict", "build"),
    "infeasible_schedule": ("Infeasible Schedule", "error"),
}
SEVERITY_RANK = {"critical": 0, "warning": 1, "info": 2}
DEPARTMENT_ICONS = {"Machining": "precision_manufacturing", "Welding": "local_fire_department",
                    "Assembly": "handyman", "Quality": "science", "Finishing": "format_paint"}


# ------------------------------------------------------------------ parsing helpers

def ts(value: str | None) -> datetime | None:
    """timestamptz → naive plant-local datetime (the Gantt and templates work in plant time)."""
    if not value:
        return None
    return datetime.fromisoformat(value).astimezone(PLANT_TZ).replace(tzinfo=None)


def day(value: str | None) -> date | None:
    return date.fromisoformat(value[:10]) if value else None


def local_day(value: str | None) -> date | None:
    t = ts(value)
    return t.date() if t else None


def num(value) -> float:
    return float(value) if value is not None else 0.0


# ------------------------------------------------------------------ builder

def build_dataset(snap: dict) -> SimpleNamespace:
    products = {p["id"]: p for p in snap["products"]}
    by_code = {p["product_code"]: p for p in snap["products"]}
    profiles = {p["id"]: p for p in snap["profiles"]}
    work_centers = {w["id"]: w for w in snap["work_centers"]}
    machines = {m["id"]: m for m in snap["machines"]}
    warehouses = {w["id"]: w for w in snap["warehouses"]}
    orders = {o["id"]: o for o in snap["sales_orders"]}
    items_by_order = defaultdict(list)
    for it in snap["sales_order_items"]:
        items_by_order[it["sales_order_id"]].append(it)
    lines = {it["id"]: it for it in snap["sales_order_items"]}

    def person(uid: str | None, fallback: str = "—") -> str:
        return profiles.get(uid, {}).get("full_name", fallback) if uid else fallback

    # ---- inventory & purchasing
    inv_by_product = defaultdict(lambda: {"on_hand": 0.0, "reserved": 0.0, "hold": 0.0, "rows": 0})
    for b in snap["inventory_balances"]:
        agg = inv_by_product[b["product_id"]]
        agg["on_hand"] += num(b["on_hand_quantity"])
        agg["reserved"] += num(b["reserved_quantity"])
        agg["hold"] += num(b["quality_hold_quantity"])
        agg["rows"] += 1
    po_headers = {p["id"]: p for p in snap["purchase_orders"]}
    po_lines = snap["purchase_order_items"]
    open_po_by_product = defaultdict(list)
    for li in po_lines:
        if li["status"] not in ("Received", "Cancelled") and num(li["open_quantity"]) > 0:
            open_po_by_product[li["product_id"]].append(li)
    hold_po_by_product = {li["product_id"]: po_headers[li["purchase_order_id"]]["po_number"]
                          for li in po_lines if li["quality_status"] == "QC Hold"}

    # ---- alerts
    alerts_by_order = defaultdict(list)
    for a in snap["planning_alerts"]:
        if a["status"] in ("Open", "Acknowledged") and a.get("sales_order_item_id") in lines:
            alerts_by_order[lines[a["sales_order_item_id"]]["sales_order_id"]].append(a)
    late_po_numbers = {a["details"].get("po_number") for a in snap["planning_alerts"]
                       if a["alert_type"] == "late_supplier_receipt" and a["status"] in ("Open", "Acknowledged")}
    required_by_po = {a["details"]["po_number"]: day(a["details"].get("required_date"))
                      for a in snap["planning_alerts"] if a["details"].get("po_number")}
    order_by_po = {a["details"]["po_number"]: orders[lines[a["sales_order_item_id"]]["sales_order_id"]]["order_number"]
                   for a in snap["planning_alerts"]
                   if a["details"].get("po_number") and a.get("sales_order_item_id") in lines}

    # ---- engineering
    boms_by_product = defaultdict(list)
    for h in snap["bom_headers"]:
        boms_by_product[h["product_id"]].append(h)
    bom_lines = defaultdict(list)
    for bi in snap["bom_items"]:
        bom_lines[bi["bom_header_id"]].append(bi)

    def active_bom(product_id: str) -> dict | None:
        heads = boms_by_product.get(product_id, [])
        approved = [h for h in heads if h["status"] == "Approved"]
        if approved:
            return approved[0]
        open_heads = sorted((h for h in heads if h["status"] in ("Draft", "Under Review")),
                            key=lambda h: h["created_at"], reverse=True)
        return open_heads[0] if open_heads else None

    def inventory_fields(product_id: str) -> dict:
        agg = inv_by_product.get(product_id)
        notes = []
        if agg and agg["hold"] > 0:
            po = hold_po_by_product.get(product_id)
            notes.append(f"{agg['hold']:g} on QC hold" + (f" ({po})" if po else ""))
        incoming = open_po_by_product.get(product_id, [])
        if incoming:
            li = incoming[0]
            po_no = po_headers[li["purchase_order_id"]]["po_number"]
            eta = day(li["expected_delivery_date"])
            notes.append(f"{po_no} ETA {eta:%b %d}" if eta else f"{po_no} open")
        return {
            "on_hand": agg["on_hand"] if agg else None,
            "reserved": agg["reserved"] if agg else 0,
            "quality_hold": agg["hold"] if agg else 0,
            "inventory_note": " · ".join(notes),
            "incoming_po": po_headers[incoming[0]["purchase_order_id"]]["po_number"] if incoming else None,
        }

    def bom_tree(head: dict) -> list[BomItem]:
        root_product = products[head["product_id"]]
        items = [BomItem(id="A0", parent=None, component_code=root_product["product_code"], name=root_product["name"],
                         item_type=ItemType.ASSY, quantity_per_parent=1)]

        def walk(header: dict, parent_id: str, seen: frozenset):
            for line in sorted(bom_lines[header["id"]], key=lambda x: x["line_number"]):
                comp = products[line["component_product_id"]]
                child_head = active_bom(comp["id"]) if comp["item_type"] == "manufactured" else None
                node_id = f"{parent_id}.{line['line_number']}"
                kind = (ItemType.SUB_ASSY if child_head else
                        ItemType.PUR if comp["item_type"] == "purchased" else ItemType.MFG)
                items.append(BomItem(id=node_id, parent=parent_id, component_code=comp["product_code"],
                                     name=comp["name"], material=comp.get("body_material") or "", item_type=kind,
                                     quantity_per_parent=num(line["quantity_per_parent"]),
                                     unit_of_measure=line["unit_of_measure"], scrap_factor=num(line["scrap_factor"]),
                                     **inventory_fields(comp["id"])))
                if child_head and comp["id"] not in seen:
                    walk(child_head, node_id, seen | {comp["id"]})

        walk(head, "A0", frozenset({head["product_id"]}))
        return items

    finished = [p for p in snap["products"] if p["item_type"] == "finished_good"]
    PRODUCTS = [Product(
        product_code=p["product_code"], name=p["name"], family=p.get("product_family") or "",
        valve_type=p.get("valve_type") or "", size=p.get("size") or "", pressure_class=p.get("pressure_class") or "",
        body_material=p.get("body_material") or "", trim_material=p.get("trim_material") or "",
        connection_type=p.get("connection_type") or "", status=p["product_status"],
        design_standard=p["attributes"].get("design_standard", ""), fire_safe=p["attributes"].get("fire_safe", ""),
        sour_service=p["attributes"].get("sour_service", ""),
        hydro_test_pressure=(f"{p['attributes']['hydro_shell_psi']:,} PSI (Shell)" if "hydro_shell_psi" in p["attributes"] else ""),
        std_lead_time_days=num(p.get("standard_lead_time_days")),
    ) for p in finished]

    BOMS, BOM_REVISIONS = {}, {}
    for p in finished:
        head = active_bom(p["id"])
        if head:
            BOMS[p["product_code"]] = BomHeader(
                id=head["id"], product_code=p["product_code"], revision=head["revision"], status=head["status"],
                effective_from=day(head["effective_from"]), approved_by=person(head["approved_by"], "— (not approved)"),
                eco_number=head.get("eco_number") or "—", plm_source="Supabase (dev)", items=bom_tree(head))
        BOM_REVISIONS[p["product_code"]] = [
            {"id": h["id"], "revision": h["revision"], "status": h["status"], "effective_from": day(h["effective_from"]),
             "effective_to": day(h["effective_to"]), "eco_number": h.get("eco_number") or "—",
             "approved_by": person(h["approved_by"], "—")}
            for h in sorted(boms_by_product.get(p["id"], []), key=lambda h: h["created_at"], reverse=True)]

    routings_by_product = {r["product_id"]: r for r in snap["routing_headers"] if r["status"] == "Approved"}
    route_ops = defaultdict(list)
    for o in snap["routing_operations"]:
        route_ops[o["routing_header_id"]].append(o)
    op_by_id = {o["id"]: o for o in snap["routing_operations"]}
    preds = defaultdict(list)
    for dep in snap["routing_dependencies"]:
        preds[dep["operation_id"]].append(op_by_id[dep["predecessor_id"]]["operation_number"])

    def duration_label(o: dict) -> str:
        setup, cycle = num(o["setup_time_minutes"]), num(o["cycle_time_minutes"])
        unit = "batch" if o["time_basis"] == "per_batch" else "unit"
        return f"Setup {setup:g}m | Cycle {cycle:g}m / {unit}" if setup else f"{cycle:g}m / {unit}"

    ROUTINGS = {}
    for p in finished:
        r = routings_by_product.get(p["id"])
        if not r:
            continue
        ops = sorted(route_ops[r["id"]], key=lambda o: o["sequence"])
        ROUTINGS[p["product_code"]] = RoutingHeader(product_code=p["product_code"], revision=r["revision"], status=r["status"], operations=[
            RoutingOperation(
                operation_number=o["operation_number"], name=o["operation_name"],
                work_center=work_centers[o["work_center_id"]]["name"], setup_minutes=int(num(o["setup_time_minutes"])),
                cycle_minutes=int(num(o["cycle_time_minutes"])), duration_label=duration_label(o),
                predecessor=", ".join(sorted(preds[o["id"]])) or "Material release",
                note="" if o["is_quality_gate"] else (o.get("notes") or ""), min_batch=o.get("minimum_batch_quantity"),
                is_mandatory_gate=o["is_quality_gate"], gate_detail=(o.get("notes") or "") if o["is_quality_gate"] else "")
            for o in ops])

    # ---- schedule versions
    runs = {r["id"]: r for r in snap["planning_runs"]}
    versions = snap["schedule_versions"]
    baseline = next((v for v in versions if v["is_baseline"]), versions[0] if versions else None)
    base_metrics = baseline["metrics"] if baseline else {}
    prod_orders = {po["id"]: po for po in snap["production_orders"]}

    def order_number_for_item(item_id: str | None) -> str | None:
        return orders[lines[item_id]["sales_order_id"]]["order_number"] if item_id in lines else None

    # Components consumed at each routing operation number, per finished product (BOM operation_reference)
    consumed_at = defaultdict(set)  # (product_id, op_number) -> component ids (including sub-assembly children)
    for p in finished:
        head = active_bom(p["id"])
        if not head:
            continue
        for line in bom_lines[head["id"]]:
            if not line.get("operation_reference"):
                continue
            stack = [line["component_product_id"]]
            while stack:
                cid = stack.pop()
                consumed_at[(p["id"], line["operation_reference"])].add(cid)
                child = active_bom(cid)
                if child:
                    stack.extend(bl["component_product_id"] for bl in bom_lines[child["id"]])
    late_components = {li["product_id"] for li in po_lines
                       if po_headers[li["purchase_order_id"]]["po_number"] in late_po_numbers}
    at_risk_orders = {orders[lines[a["sales_order_item_id"]]["sales_order_id"]]["order_number"]
                      for a in snap["planning_alerts"]
                      if a["alert_type"] == "delivery_date_at_risk" and a.get("sales_order_item_id") in lines}

    finish_by_version = defaultdict(dict)  # version id -> {order number: planned completion (plant date)}
    for pord in snap["production_orders"]:
        so_number = order_number_for_item(pord.get("sales_order_item_id"))
        finish = local_day(pord.get("planned_finish"))
        if so_number and finish:
            current_max = finish_by_version[pord["schedule_version_id"]].get(so_number)
            finish_by_version[pord["schedule_version_id"]][so_number] = max(finish, current_max) if current_max else finish

    SCHEDULE_VERSIONS = {}
    for v in versions:
        run = runs.get(v["planning_run_id"], {})
        assumptions = run.get("scenario_assumptions") or {}
        ops, deps = [], []
        for pop in snap["production_operations"]:
            pord = prod_orders.get(pop["production_order_id"])
            if not pord or pord["schedule_version_id"] != v["id"]:
                continue
            notes = pop.get("notes") or ""
            if pop.get("assigned_machine_id"):
                machine_code = machines[pop["assigned_machine_id"]]["machine_code"]
            elif notes.startswith("Scenario machine "):      # hypothetical second unit of a what-if scenario (M10)
                machine_code = notes.removeprefix("Scenario machine ").split(":")[0]
            else:
                continue
            # scenario orders (urgent order) have no sales order line: PRD-SIM-1-1 -> SIM-1
            so_number = order_number_for_item(pord.get("sales_order_item_id")) or                 pord["production_order_number"].removeprefix("PRD-").rsplit("-", 1)[0]
            if not so_number or not pop.get("planned_start"):
                continue
            rop = op_by_id.get(pop["routing_operation_id"], {})
            consumed = consumed_at.get((pord["product_id"], pop["operation_number"]), set())
            ops.append(ScheduledOperation(
                op_code=pop["operation_number"], name=rop.get("operation_name", pop["operation_number"]),
                order_number=so_number, machine_code=machine_code,
                start=ts(pop["planned_start"]), end=ts(pop["planned_finish"]), note=pop.get("notes") or "",
                critical_path=so_number in at_risk_orders, shortage_blocked=bool(consumed & late_components)))
        # precedence arrows between operations of the same production order
        ops_by_order = defaultdict(set)
        for o in ops:
            ops_by_order[o.order_number].add(o.op_code)
        for dep in snap["routing_dependencies"]:
            op_no, pred_no = op_by_id[dep["operation_id"]]["operation_number"], op_by_id[dep["predecessor_id"]]["operation_number"]
            for so_number, codes in ops_by_order.items():
                if op_no in codes and pred_no in codes:
                    deps.append((f"{so_number}:{pred_no}", f"{so_number}:{op_no}"))
        blocks = [PmBlock(machine_code=machines[d["machine_id"]]["machine_code"], start=ts(d["start_datetime"]),
                          end=ts(d["end_datetime"]), label=d["reason"],
                          kind="downtime" if d["downtime_type"] == "breakdown" else "pm")
                  for d in snap["machine_downtime"] if d["status"] != "Cancelled"]
        for change in assumptions.get("changes") or []:       # what-if scenarios (M10)
            prm = change.get("params") or {}
            if change.get("kind") == "machine_unavailable":
                blocks.append(PmBlock(machine_code=prm["machine_code"], start=datetime.fromisoformat(prm["start"]),
                                      end=datetime.fromisoformat(prm["end"]), label="Scenario: unavailable", kind="downtime"))
            elif change.get("kind") == "overtime":
                wc_id = next((w["id"] for w in snap["work_centers"] if w["work_center_code"] == prm["work_center_code"]), None)
                for mc in snap["machines"]:
                    if mc["work_center_id"] == wc_id:
                        blocks.append(PmBlock(machine_code=mc["machine_code"], start=datetime.fromisoformat(prm["start"]),
                                              end=datetime.fromisoformat(prm["end"]), label="Scenario OT", kind="ot"))
        ot = assumptions.get("overtime")
        if ot:
            blocks.append(PmBlock(machine_code=ot["machine_code"], start=ts(ot["starts_at"]), end=ts(ot["ends_at"]),
                                  label="Scenario OT", kind="ot"))
        markers = []
        expedite = assumptions.get("expedite") or {}
        product_of_order = {order_number_for_item(pp.get("sales_order_item_id")): pp["product_id"]
                            for pp in prod_orders.values() if pp["schedule_version_id"] == v["id"]}
        for li in po_lines:
            po_no = po_headers[li["purchase_order_id"]]["po_number"]
            if po_no not in late_po_numbers:
                continue
            eta = day(expedite["new_expected_date"]) if expedite.get("po_number") == po_no else day(li["expected_delivery_date"])
            # The material marker sits on the lane of the first operation that consumes the late component.
            consumer = next((o for o in ops
                             if li["product_id"] in consumed_at.get((product_of_order.get(o.order_number), o.op_code), set())),
                            None)
            if eta and consumer:
                markers.append(MaterialMarker(machine_code=consumer.machine_code,
                                              at=datetime.combine(eta, datetime.min.time()) + timedelta(hours=8),
                                              label=f"{po_no} MATL"))
        m = v.get("metrics") or {}
        SCHEDULE_VERSIONS[f"v{v['version_number']}"] = ScheduleVersion(
            key=f"v{v['version_number']}", version_number=v["version_number"], label=v.get("label") or f"Version {v['version_number']}",
            status=v["status"], is_baseline=v["is_baseline"], run_number=run.get("run_number", 0),
            solver_status=(run.get("solver_status") or "Pending").upper().replace(" ", "_"),
            solve_seconds=num(run.get("solve_seconds")), approved_by=person(v.get("approved_by"), None),
            created_by=person(v.get("created_by")), change_summary=v.get("change_summary") or "",
            operations=ops, blocks=blocks, markers=markers, dependencies=deps,
            late_orders=int(m.get("late_orders", 0)), total_lateness_h=int(m.get("total_lateness_hours", 0)),
            overtime_cost=num(m.get("overtime_cost")), hard_violations=int(m.get("hard_violations", 0)),
            soft_violations=int(m.get("soft_violations", 0)), validation_passed=v.get("validation_passed"),
            created_by_id=v.get("created_by"), order_finish=finish_by_version[v["id"]],
            report=m if "lines" in m else {}, run_type=run.get("run_type") or "schedule", assumptions=assumptions)

    # ---- capacity (loads are planning-run outputs stored on the baseline version)
    wc_load = base_metrics.get("work_center_load", {})
    machine_util = base_metrics.get("machine_utilization", {})
    machines_by_wc = defaultdict(list)
    for mc in snap["machines"]:
        machines_by_wc[mc["work_center_id"]].append(mc)
    WORK_CENTERS = []
    for w in snap["work_centers"]:
        load = wc_load.get(w["work_center_code"], {})
        mcs = machines_by_wc[w["id"]]
        WORK_CENTERS.append(WorkCenter(
            code=w["work_center_code"], name=w["name"], detail=w.get("description") or "", group=w["name"],
            group_icon=DEPARTMENT_ICONS.get(w.get("department"), "factory"),
            unit_label=f"{len(mcs)} Unit{'s' if len(mcs) != 1 else ''}",
            scheduled_hours=num(load.get("scheduled_hours")), capacity_hours=num(load.get("capacity_hours")) or 240,
            queue_note=load.get("queue_note", ""),
            machines=[Machine(machine_code=mc["machine_code"], name=mc["name"], description=mc.get("description") or "",
                              utilization=num(machine_util.get(mc["machine_code"])),
                              down=num(machine_util.get(mc["machine_code"])) >= 100) for mc in mcs]))

    # ---- sales orders
    finish_by_order = defaultdict(list)
    for pord in snap["production_orders"]:
        if baseline and pord["schedule_version_id"] == baseline["id"] and pord.get("planned_finish"):
            so_number = order_number_for_item(pord.get("sales_order_item_id"))
            if so_number:
                finish_by_order[so_number].append(local_day(pord["planned_finish"]))

    # Material requirements planning on the same snapshot (M08): allocation-aware staging and readiness.
    mrp = run_mrp(mrp_input(snap))

    def mrp_staging(order_number: str) -> list[MaterialStage]:
        by_item: dict[str, list] = defaultdict(list)
        for r in mrp.for_order(order_number):
            if r.level == 1:
                by_item[r.item].append(r)
        stages = []
        for item_code, reqs in by_item.items():
            required = sum(r.gross for r in reqs)
            covered = sum(r.allocated + r.planned_production for r in reqs if r.status != "short") + \
                sum(r.allocated for r in reqs if r.status == "short")
            statuses = {r.status for r in reqs}
            status = "short" if "short" in statuses else "late" if "late" in statuses else "covered"
            ready = max((r.ready_date for r in reqs if r.ready_date), default=None)
            receipts = [a.ref.split("/")[0] for r in reqs for a in r.allocations if a.source == "po"]
            note = {"covered": "Covered" + (f" · ready {ready:%b %d}" if ready and ready > mrp.input.as_of else ""),
                    "late": f"Late · ready {ready:%b %d}" if ready else "Late",
                    "short": f"Short {sum(mrp.shortage_units(r) for r in reqs if r.status == 'short'):g}"}[status]
            stages.append(MaterialStage(
                name=by_code[item_code]["name"] if item_code in by_code else item_code,
                staged=int(round(min(covered, required))), required=int(-(-round(required, 6) // 1)), note=note,
                blocked_by=(receipts[-1] if receipts and status == "late" else
                            ("QC hold" if status == "short" and item_code in mrp.input.holds else None)
                            if status != "covered" else None)))
        return stages

    def mrp_ready(order_number: str, line_numbers: list[int]) -> date | None:
        dates = [mrp.line_ready_date(order_number, n) for n in line_numbers]
        if any(d is None for d in dates) or not dates:
            return None
        return max(dates)

    SALES_ORDERS = []
    for o in sorted(snap["sales_orders"], key=lambda x: x["order_number"]):
        its = sorted(items_by_order[o["id"]], key=lambda x: x["line_number"])
        if not its:
            continue
        first = its[0]
        prod = products[first["product_id"]]
        bom_head = next((h for h in snap["bom_headers"] if h["id"] == first.get("bom_revision_id")), None)
        rout_head = next((r for r in snap["routing_headers"] if r["id"] == first.get("routing_revision_id")), None)
        alerts = sorted(alerts_by_order[o["id"]], key=lambda a: SEVERITY_RANK.get(a["severity"], 9))
        top = alerts[0] if alerts else None
        label, icon = ALERT_LABELS.get(top["alert_type"], ("Planning Alert", "info")) if top else ("None", "task_alt")
        data_complete = all(i.get("bom_revision_id") and i.get("routing_revision_id") for i in its)
        material_alert = any(a["alert_type"] in ("material_shortage", "late_supplier_receipt") for a in alerts)
        quantity = sum(num(i["quantity"]) for i in its)
        wcs = sorted({work_centers[op["work_center_id"]]["work_center_code"] for op in route_ops.get(rout_head["id"], [])}) if rout_head else []
        staging = mrp_staging(o["order_number"]) if bom_head else []
        diagnostic = ("From planning run alerts: " + " ".join(a["description"] for a in alerts)) if alerts \
            else "No open planning alerts for this order."
        SALES_ORDERS.append(SalesOrder(
            order_number=o["order_number"], customer_name=o["customer_name"], customer_reference=o.get("customer_reference") or "",
            customer_profile=o.get("notes") or "", customer_otd=None, product_code=prod["product_code"],
            spec_title=prod["name"], spec_detail=" / ".join(x for x in (prod.get("body_material"), prod.get("trim_material"), prod.get("connection_type")) if x),
            tag=(first.get("product_configuration") or {}).get("tags", ""), quantity=int(quantity),
            lines=[SalesOrderLine(line_number=i["line_number"], description=i.get("notes") or products[i["product_id"]]["name"],
                                  quantity=int(num(i["quantity"])), tags=(i.get("product_configuration") or {}).get("tags", ""),
                                  note=i["status"]) for i in its],
            bom_revision=bom_head["revision"] if bom_head else "— Missing", bom_status=bom_head["status"] if bom_head else "Missing",
            routing_revision=rout_head["revision"] if rout_head else "— Missing", work_centers=" / ".join(wcs) or "—",
            order_date=day(o["order_date"]), requested_delivery_date=day(o["requested_delivery_date"]),
            projected_completion=max(finish_by_order[o["order_number"]]) if finish_by_order[o["order_number"]] else None,
            priority=Priority(o["priority"]), status=OrderStatus(o["status"]),
            value_usd=sum(num(i["quantity"]) * num(i.get("unit_price")) for i in its),
            limiting_constraint=label, constraint_detail=top["description"] if top else "No open alerts",
            constraint_icon=icon, recommended_action=(top.get("recommended_action") or "—") if top else "—",
            readiness=("Needs Data" if not data_complete else "Material Shortage" if material_alert
                       else label if top else "Cleared"),
            material_ready=mrp_ready(o["order_number"], [i["line_number"] for i in its]) if data_complete else None,
            staging=staging, ai_diagnostic=diagnostic,
            signoff=(f"{person(bom_head['approved_by'], 'Approved')} on {local_day(bom_head['approved_at']):%b %d, %Y}"
                     if bom_head and bom_head.get("approved_at") else "— pending"),
            critical_shortage=material_alert and any(a["severity"] == "critical" for a in alerts),
            data_complete=data_complete))

    # ---- purchasing view rows (one per PO line)
    used_by = defaultdict(list)  # component id -> order numbers whose BOM uses it (direct or via sub-assemblies)
    for so in SALES_ORDERS:
        bom = BOMS.get(so.product_code)
        for it in (bom.items if bom else []):
            comp = by_code.get(it.component_code)
            if comp:
                used_by[comp["id"]].append(so.order_number)
    PURCHASE_ORDERS = []
    for li in po_lines:
        head = po_headers[li["purchase_order_id"]]
        prod = products[li["product_id"]]
        po_no = head["po_number"]
        status = ("Late" if po_no in late_po_numbers else "QC Hold" if li["quality_status"] == "QC Hold" else li["status"])
        PURCHASE_ORDERS.append(PurchaseOrder(
            po_number=po_no, supplier_name=head["supplier_name"], item=prod["name"], detail=prod["product_code"],
            linked_order=order_by_po.get(po_no) or (used_by.get(prod["id"]) or ["—"])[0],
            ordered_quantity=int(num(li["ordered_quantity"])), received_quantity=int(num(li["received_quantity"])),
            required_date=required_by_po.get(po_no), expected_delivery_date=day(li["expected_delivery_date"]),
            status=status, quality_status="" if li["quality_status"] == "Not Inspected" else li["quality_status"],
            note=head.get("notes") or ""))

    INVENTORY = [{
        "id": b["id"], "product_code": products[b["product_id"]]["product_code"], "product_name": products[b["product_id"]]["name"],
        "warehouse_code": warehouses[b["warehouse_id"]]["warehouse_code"], "lot_number": b["lot_number"],
        "on_hand": num(b["on_hand_quantity"]), "reserved": num(b["reserved_quantity"]),
        "quality_hold": num(b["quality_hold_quantity"]), "net_available": num(b["net_available_quantity"])}
        for b in sorted(snap["inventory_balances"], key=lambda b: products[b["product_id"]]["product_code"])]
    PO_LINES = [{
        "id": li["id"], "po_number": po_headers[li["purchase_order_id"]]["po_number"], "line_number": li["line_number"],
        "supplier_name": po_headers[li["purchase_order_id"]]["supplier_name"],
        "product_code": products[li["product_id"]]["product_code"], "product_name": products[li["product_id"]]["name"],
        "ordered": num(li["ordered_quantity"]), "received": num(li["received_quantity"]), "open": num(li["open_quantity"]),
        "expected": day(li["expected_delivery_date"]), "status": li["status"], "quality_status": li["quality_status"],
        "warehouse_code": warehouses[li["warehouse_id"]]["warehouse_code"], "notes": li.get("notes") or ""}
        for li in sorted(po_lines, key=lambda li: (po_headers[li["purchase_order_id"]]["po_number"], li["line_number"]))]

    APPROVAL_REQUESTS = []
    for r in snap["approval_requests"]:
        v = next((x for x in versions if x["id"] == r["schedule_version_id"]), None)
        if not v:
            continue
        m = v.get("metrics") or {}
        APPROVAL_REQUESTS.append(ApprovalRequest(
            id=r["request_number"], schedule_version=f"v{v['version_number']}",
            run_number=runs.get(v["planning_run_id"], {}).get("run_number", 0),
            requested_by=person(r.get("requested_by")), approver=person(r.get("approver_id"), "Authorized approver"),
            status=r["status"], compiled_at=f"{ts(r['requested_at']):%b %d, %H:%M} CST",
            total_ops=len(SCHEDULE_VERSIONS[f"v{v['version_number']}"].operations),
            hard_violations=int(m.get("hard_violations", 0)), soft_violations=int(m.get("soft_violations", 0)),
            oee_forecast=num(m.get("oee_forecast")) or 88.4, changeover_delta=m.get("changeover_delta", "—"),
            comments=r.get("comments") or "", requested_by_id=r.get("requested_by"),
            requested_at=ts(r.get("requested_at")), decided_at=ts(r.get("decided_at")),
            decision_comments=r.get("decision_comments") or ""))

    horizon_run = runs.get(baseline["planning_run_id"]) if baseline else None
    horizon = (day(horizon_run["planning_horizon_start"]), day(horizon_run["planning_horizon_end"])) if horizon_run \
        else mock_data.PLANNING_HORIZON
    gantt_start = datetime.combine(horizon[0] + timedelta(days=(7 - horizon[0].weekday()) % 7), datetime.min.time())

    # ---- resources & calendars (spec §12.5, M09): scheduler inputs, editable by supervisors/admins
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    def days_label(days: list[int]) -> str:
        days = sorted(days)
        if len(days) > 2 and days == list(range(days[0], days[-1] + 1)):
            return f"{day_names[days[0] - 1]}–{day_names[days[-1] - 1]}"
        return ", ".join(day_names[d - 1] for d in days)

    product_of_routing = {r["id"]: products[r["product_id"]]["product_code"] for r in snap["routing_headers"]
                          if r["status"] == "Approved"}
    RESOURCES = {"work_centers": [], "downtime": [], "exceptions": []}
    for w in snap["work_centers"]:
        mcs = sorted((m for m in snap["machines"] if m["work_center_id"] == w["id"]), key=lambda m: m["machine_code"])
        RESOURCES["work_centers"].append({
            "id": w["id"], "code": w["work_center_code"], "name": w["name"], "department": w.get("department") or "",
            "description": w.get("description") or "",
            "machines": [{"id": m["id"], "code": m["machine_code"], "name": m["name"],
                          "capabilities": m.get("capability_tags") or [], "factor": num(m.get("capacity_factor")) or 1,
                          "active": m["is_active"],
                          "shifts": [s["shift_name"] for s in snap.get("shift_calendars", []) if s.get("machine_id") == m["id"]]}
                         for m in mcs],
            "shifts": [{"name": s["shift_name"], "start": s["start_time"][:5], "end": s["end_time"][:5],
                        "days": days_label(s["working_days"]), "valid_from": s.get("valid_from"),
                        "valid_to": s.get("valid_to")}
                       for s in snap.get("shift_calendars", []) if s.get("work_center_id") == w["id"]],
            "routing_ops": [f"{product_of_routing[o['routing_header_id']]} {o['operation_number']} {o['operation_name']}"
                            for o in sorted(snap["routing_operations"], key=lambda o: o["sequence"])
                            if o["work_center_id"] == w["id"] and o["routing_header_id"] in product_of_routing],
        })
    for d in snap["machine_downtime"]:
        RESOURCES["downtime"].append({
            "id": d["id"], "machine_code": machines[d["machine_id"]]["machine_code"], "start": ts(d["start_datetime"]),
            "end": ts(d["end_datetime"]), "type": d["downtime_type"], "status": d["status"], "reason": d["reason"],
            "by": person(d.get("created_by"), "seed")})
    for e in snap.get("calendar_exceptions", []):
        scope = (machines[e["machine_id"]]["machine_code"] if e.get("machine_id")
                 else work_centers[e["work_center_id"]]["work_center_code"] if e.get("work_center_id") else "Plant-wide")
        RESOURCES["exceptions"].append({
            "id": e["id"], "kind": e["exception_type"], "description": e["description"], "start": ts(e["starts_at"]),
            "end": ts(e["ends_at"]), "scope": scope, "approved": e["is_approved"]})
    RESOURCES["downtime"].sort(key=lambda d: d["start"])
    RESOURCES["exceptions"].sort(key=lambda e: e["start"])

    return SimpleNamespace(
        SOURCE="supabase", PLANT=mock_data.PLANT, PLANNING_HORIZON=horizon, GANTT_START=gantt_start,
        GANTT_DAYS=mock_data.GANTT_DAYS, PRODUCTS=PRODUCTS, BOMS=BOMS, BOM_REVISIONS=BOM_REVISIONS, ROUTINGS=ROUTINGS,
        WORK_CENTERS=WORK_CENTERS, SALES_ORDERS=SALES_ORDERS, PURCHASE_ORDERS=PURCHASE_ORDERS,
        SCHEDULE_VERSIONS=SCHEDULE_VERSIONS, ORDER_COLORS=mock_data.ORDER_COLORS, APPROVAL_REQUESTS=APPROVAL_REQUESTS,
        APPROVER=APPROVAL_REQUESTS[0].approver if APPROVAL_REQUESTS else "Authorized approver",
        APPROVER_ROLE=mock_data.APPROVER_ROLE, COPILOT_INSIGHTS=mock_data.COPILOT_INSIGHTS,
        ASSISTANT_QUICK_PROMPTS=mock_data.ASSISTANT_QUICK_PROMPTS, INVENTORY=INVENTORY, PO_LINES=PO_LINES,
        PRODUCT_ROWS={p["product_code"]: p for p in snap["products"]},
        PROFILES={p["id"]: p["full_name"] for p in snap["profiles"]},
        MRP=mrp,
        LAST_MRP_RUN=next(({"run_number": r["run_number"], "status": r["solver_status"],
                            "completed_at": ts(r.get("completed_at")), "by": person(r.get("created_by"))}
                           for r in sorted(snap["planning_runs"], key=lambda r: r["run_number"], reverse=True)
                           if r["run_type"] == "mrp"), None),
        ORDER_EDIT={o["order_number"]: {
            "order_number": o["order_number"], "customer_name": o["customer_name"],
            "customer_reference": o.get("customer_reference") or "", "order_date": o["order_date"],
            "requested_delivery_date": o["requested_delivery_date"], "priority": o["priority"], "status": o["status"],
            "notes": o.get("notes") or "",
            "lines": [{"product_code": products[i["product_id"]]["product_code"], "quantity": num(i["quantity"]),
                       "unit_price": i.get("unit_price"), "tags": (i.get("product_configuration") or {}).get("tags", ""),
                       "notes": i.get("notes") or ""}
                      for i in sorted(items_by_order[o["id"]], key=lambda x: x["line_number"])]}
            for o in snap["sales_orders"]},
        SNAPSHOT_AT=snap.get("generated_at"),
        RESOURCES=RESOURCES,
        BASELINE_IMPACT=[{"description": a["description"], "details": a.get("details") or {}, "created_at": ts(a["created_at"])}
                         for a in snap["planning_alerts"]
                         if a["status"] == "Open" and (a.get("details") or {}).get("source") == "receipt_change"],
    )


# ------------------------------------------------------------------ loading (per user token, short cache)

_cache: dict[str, tuple[float, SimpleNamespace]] = {}


class SnapshotError(Exception):
    pass


async def load_dataset(supabase_url: str, publishable_key: str, access_token: str) -> SimpleNamespace:
    cached = _cache.get(access_token)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{supabase_url.rstrip('/')}/rest/v1/rpc/planning_snapshot", json={},
                                  headers={"apikey": publishable_key, "Authorization": f"Bearer {access_token}"})
    except httpx.HTTPError as exc:
        raise SnapshotError("Supabase is unreachable") from exc
    if r.status_code != 200:
        raise SnapshotError(f"Snapshot failed (HTTP {r.status_code})")
    ds = build_dataset(r.json())
    if len(_cache) > 64:
        _cache.clear()
    _cache[access_token] = (time.monotonic() + CACHE_TTL_SECONDS, ds)
    return ds


def invalidate(access_token: str | None = None) -> None:
    """Drop cached snapshots after a write so the next page shows fresh data."""
    if access_token:
        _cache.pop(access_token, None)
    else:
        _cache.clear()
