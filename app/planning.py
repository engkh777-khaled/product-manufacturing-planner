"""Deterministic planning helpers used by the UI.

The planning engine calculates; the AI explains (spec §1). These are the small,
testable calculations the frontend needs so that displayed figures are derived
from data rather than hard-coded strings.
"""

from __future__ import annotations

import math
from datetime import date, datetime

from app.models import BomItem, RiskLevel

# Utilisation bands (dashboard legend): Normal < 85 %, Constrained 85–92 %, Bottleneck > 92 %
CONSTRAINED_PCT = 85.0
BOTTLENECK_PCT = 92.0

# Risk thresholds (spec §9 — configurable)
MEDIUM_RISK_MARGIN_DAYS = 2


def net_available(on_hand: float, reserved: float = 0, quality_hold: float = 0) -> float:
    """Net Available = On Hand − Reserved − Quality Hold (spec §6.9). Never negative."""
    return max(on_hand - reserved - quality_hold, 0.0)


def yield_factor(scrap_factor: float) -> float:
    """Yield convention: yield = 1 − scrap. Scrap is applied once, via the yield."""
    if not 0 <= scrap_factor < 1:
        raise ValueError(f"scrap factor must be in [0, 1), got {scrap_factor}")
    return 1.0 - scrap_factor


def required_qty(parent_qty: float, qty_per_parent: float, scrap_factor: float = 0.0) -> float:
    """Required = Parent Qty × Qty per Parent / Yield (spec §7.1)."""
    if qty_per_parent <= 0:
        raise ValueError("component quantity per parent must be positive")
    return parent_qty * qty_per_parent / yield_factor(scrap_factor)


def util_pct(scheduled_hours: float, capacity_hours: float) -> float:
    return 0.0 if capacity_hours <= 0 else scheduled_hours / capacity_hours * 100


def util_band(pct: float) -> str:
    """Returns 'normal' | 'constrained' | 'bottleneck'."""
    if pct > BOTTLENECK_PCT:
        return "bottleneck"
    if pct >= CONSTRAINED_PCT:
        return "constrained"
    return "normal"


def lateness_days(requested: date, projected: date | None) -> int | None:
    """Positive = late, negative = early, None = unknown (needs data)."""
    if projected is None:
        return None
    return (projected - requested).days


def risk_level(
    lateness: int | None,
    critical_shortage: bool = False,
    data_complete: bool = True,
    capacity_conflict: bool = False,
) -> RiskLevel:
    """Explicit rule-based risk classification (spec §9)."""
    if not data_complete or lateness is None:
        return RiskLevel.UNKNOWN
    if lateness > 0 or critical_shortage or capacity_conflict:
        return RiskLevel.HIGH
    if -lateness < MEDIUM_RISK_MARGIN_DAYS:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


# --- BOM explosion --------------------------------------------------------------


def explode_bom(items: list[BomItem], order_qty: float = 1, scrap_delta: float = 0.0) -> list[dict]:
    """Flatten a multilevel BOM into display rows with depth, ancestors and net requirements.

    Requirements propagate down the tree: a child's gross requirement is its parent's
    requirement × qty-per / yield. `scrap_delta` (fraction) simulates a scrap increase.
    Raises ValueError on circular references (spec §6.3).
    """
    children: dict[str | None, list[BomItem]] = {}
    for item in items:
        children.setdefault(item.parent, []).append(item)

    rows: list[dict] = []

    def walk(parent_id: str | None, parent_req: float, depth: int, ancestors: list[str]) -> None:
        kids = children.get(parent_id, [])
        for idx, item in enumerate(kids):
            if item.id in ancestors:
                raise ValueError(f"circular BOM reference at {item.component_code}")
            scrap = min(item.scrap_factor + scrap_delta, 0.95)
            req = required_qty(parent_req, item.quantity_per_parent, scrap)
            available = None if item.on_hand is None else net_available(
                item.on_hand, item.reserved, item.quality_hold)
            shortage = 0.0 if available is None else max(math.ceil(round(req, 6)) - available, 0)
            rows.append({
                "item": item,
                "depth": depth,
                "ancestors": list(ancestors),
                "has_children": item.id in children,
                "is_last": idx == len(kids) - 1,
                "scrap": scrap,
                "yield": yield_factor(scrap),
                "net_req": req,
                "available": available,
                "shortage": shortage,
            })
            walk(item.id, req, depth + 1, ancestors + [item.id])

    walk(None, order_qty, 0, [])
    shortage_ids = {r["item"].id for r in rows if r["shortage"] > 0}
    flagged: set[str] = set()
    for r in rows:
        if r["item"].id in shortage_ids:
            flagged.update(r["ancestors"])
            flagged.add(r["item"].id)
    for r in rows:
        r["on_shortage_path"] = r["item"].id in flagged
    return rows


def find_cycles(items: list[BomItem]) -> list[str]:
    """Component codes whose parent chain loops back on itself."""
    parent = {i.id: i.parent for i in items}
    code = {i.id: i.component_code for i in items}
    cyclic: set[str] = set()
    for start in parent:
        seen, node = [], start
        while node is not None and node in parent:
            if node in seen:
                cyclic.update(code[n] for n in seen[seen.index(node):])
                break
            seen.append(node)
            node = parent[node]
    return sorted(cyclic)


def validate_bom(items: list[BomItem], status: str, build_qty: float = 1) -> list[dict]:
    """Independent BOM checks for the 'Run BOM Explosion Validation' action."""
    checks: list[dict] = []
    checks.append({
        "name": "Approved & effective revision",
        "ok": status == "Approved",
        "detail": f"Revision status: {status}",
    })
    ids = {i.id for i in items}
    orphans = [i.component_code for i in items if i.parent and i.parent not in ids]
    checks.append({
        "name": "Parent references resolve",
        "ok": not orphans,
        "detail": "All nodes linked" if not orphans else f"Orphans: {', '.join(orphans)}",
    })
    cyclic = find_cycles(items)
    rows = explode_bom(items, build_qty) if not cyclic else []
    checks.append({
        "name": "No circular references",
        "ok": not cyclic,
        "detail": f"{len(rows)} nodes traversed" if not cyclic else f"Cycle through: {', '.join(cyclic)}",
    })
    checks.append({
        "name": "Positive component quantities",
        "ok": all(i.quantity_per_parent > 0 for i in items),
        "detail": "qty/parent > 0 for every line",
    })
    missing = [i.component_code for i in items if i.on_hand is None and i.item_type.value in ("MFG", "PUR")
               and not i.incoming_po and i.parent is not None]
    checks.append({
        "name": "Inventory record for every leaf",
        "ok": not missing,
        "detail": "Complete" if not missing else f"No balance: {', '.join(missing)}",
    })
    short = [r["item"].component_code for r in rows if r["shortage"] > 0]
    checks.append({
        "name": f"Material covers {build_qty:g}-unit build",
        "ok": not short,
        "detail": "No shortages" if not short else f"Short: {', '.join(short)}",
    })
    return checks


# --- Gantt geometry -------------------------------------------------------------


def hours_between(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 3600
