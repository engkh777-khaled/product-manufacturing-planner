"""Scripted AI assistant for the demo build.

No LLM is called. Replies are assembled from repository data so that every number
comes from the planning data, never invented (spec §11.3 rule 1). The tool names
mirror the server-side tools listed in spec §11.1 so the UI trace is realistic.
"""

from __future__ import annotations

import re

from app.data import repository as repo
from app.models import RiskLevel


def _order_answer(order_number: str) -> dict:
    view = repo.get_order(order_number)
    if view is None:
        return {"tools": ["get_order_details"], "status": "NOT_FOUND",
                "paragraphs": [f"I could not find sales order {order_number} in the planning data."], "links": []}
    o = view["o"]
    if not o.data_complete:
        return {
            "tools": ["get_order_details", "validate_order_readiness"],
            "status": "NEEDS_DATA",
            "paragraphs": [
                f"{o.order_number} ({o.customer_name}) cannot be scheduled yet: {o.constraint_detail}.",
                f"BOM {o.bom_revision} is {o.bom_status}; routing: {o.routing_revision}. I will not estimate a "
                f"completion date without approved engineering data.",
            ],
            "links": [("Open engineering record", f"/engineering?product={o.product_code}")],
        }
    req = o.requested_delivery_date.strftime("%d %b")
    proj = o.projected_completion.strftime("%d %b")
    if view["late"] > 0:
        verdict = (f"Not under the current baseline. The proposed schedule finishes on {proj}, "
                   f"{view['late']} day(s) after the requested date of {req}.")
    else:
        verdict = f"Yes, under the current baseline. Projected completion is {proj} against a requested date of {req} ({view['variance']})."
    return {
        "tools": ["get_order_details", "validate_order_readiness", "check_material_availability",
                  "get_schedule_status(v4.2)", "explain_delivery_risk"],
        "status": "FEASIBLE_WITH_LATENESS" if view["late"] > 0 else "FEASIBLE",
        "paragraphs": [
            verdict,
            f"Limiting constraint: {o.limiting_constraint} — {o.constraint_detail}. Risk classification: {view['risk'].value}.",
            o.ai_diagnostic,
        ],
        "links": [(f"Open {o.order_number}", f"/orders?selected={o.order_number}"), ("View in Workbench", "/workbench")],
    }


def _bottleneck_answer() -> dict:
    views = sorted(repo.work_center_views(), key=lambda v: -v["pct"])
    b = views[0]
    runner = views[1]
    return {
        "tools": ["get_production_capacity"],
        "status": "OK",
        "paragraphs": [
            f"The current bottleneck is {b['wc'].code} {b['wc'].name} at {b['pct']:.0f}% of 2-shift capacity "
            f"({b['wc'].scheduled_hours:.1f}h of {b['wc'].capacity_hours:.0f}h). {b['wc'].queue_note}.",
            f"Next most loaded: {runner['wc'].code} {runner['wc'].name} at {runner['pct']:.0f}% ({runner['band']}).",
            "Hydrostatic Rig 1 also has confirmed downtime Nov 24–25 (pump overhaul), which compresses the available window.",
        ],
        "links": [("Open Workbench", "/workbench")],
    }


def _compare_answer() -> dict:
    b, c = repo.get_version("v4.2"), repo.get_version("v4.3")
    return {
        "tools": ["get_schedule_status(v4.2)", "get_schedule_status(v4.3)"],
        "status": "OK",
        "paragraphs": [
            f"v{c.version_number} (run #{c.run_number}) differs from baseline v{b.version_number} by: {c.change_summary}.",
            f"Late orders {b.late_orders} → {c.late_orders}; total lateness {b.total_lateness_h}h → {c.total_lateness_h}h; "
            f"added cost ${c.overtime_cost:,.0f}. Hard violations: {c.hard_violations}. v{c.version_number} is {c.status} — "
            f"it is not the approved baseline until {repo.approval_requests()[0].approver} signs off.",
        ],
        "links": [("Compare in Workbench", "/workbench?version=v4.3")],
    }


def _risk_answer() -> dict:
    risky = [v for v in repo.list_orders() if v["risk"] in (RiskLevel.HIGH, RiskLevel.UNKNOWN)]
    lines = [f"{v['o'].order_number} ({v['o'].customer_name}): {v['risk'].value} — {v['variance']}, {v['o'].limiting_constraint}"
             for v in risky]
    return {
        "tools": ["explain_delivery_risk(all_open_orders)"],
        "status": "OK",
        "paragraphs": [f"{len(risky)} orders are high risk or missing data:"] + lines,
        "links": [("Open At-Risk orders", "/orders?tab=at_risk")],
    }


def _refuse_approval() -> dict:
    return {
        "tools": [],
        "status": "POLICY",
        "paragraphs": ["I can't approve, release, or commit plans. Approval is reserved for an authorized human approver. "
                       "I can prepare the submission in the panel on the right for your review."],
        "links": [],
    }


def respond(prompt: str) -> dict:
    text = prompt.strip()
    low = text.lower()
    order = re.search(r"\bSO-\d{4}\b", text, re.IGNORECASE)
    if re.search(r"\b(approve|release|commit|place (a )?purchase)\b", low):
        return _refuse_approval()
    if order:
        return _order_answer(order.group(0).upper())
    if "bottleneck" in low or "capacity" in low or "machine" in low:
        return _bottleneck_answer()
    if "changed" in low or "compare" in low or "v4.3" in low or "difference" in low:
        return _compare_answer()
    if "risk" in low or "late" in low:
        return _risk_answer()
    return {
        "tools": [],
        "status": "DEMO",
        "paragraphs": ["This demo assistant is scripted and answers questions about specific orders (e.g. 'Why is SO-1012 late?'), "
                       "bottlenecks, at-risk orders, and the v4.2 vs v4.3 comparison."],
        "links": [],
    }
