"""MRP data structures. Quantities are floats (scrap makes requirements fractional); shortages of discrete
units are rounded up when reported."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

EPS = 1e-6
PRIORITY_RANK = {"Critical": 0, "High": 1, "Normal": 2, "Low": 3}


@dataclass(frozen=True)
class Item:
    code: str
    name: str
    item_type: str  # finished_good | manufactured | purchased
    uom: str = "EA"
    lead_time_days: float = 0.0

    @property
    def discrete(self) -> bool:
        return self.uom == "EA"


@dataclass(frozen=True)
class BomLine:
    component: str
    qty_per: float
    scrap: float = 0.0
    operation_ref: str | None = None


@dataclass
class SupplyLot:
    """One source of supply: net-available stock (available at as-of) or an eligible open PO line."""
    item: str
    quantity: float
    available: date
    source: str  # stock | po
    ref: str     # e.g. "WH-MAIN" / "WH-DOCK lot HT-9921-A" or "PO-8821/1"
    remaining: float = field(init=False)

    def __post_init__(self):
        self.remaining = self.quantity


@dataclass(frozen=True)
class Demand:
    """One sales order line, with the dates it needs material (from the baseline schedule where available)."""
    order_number: str
    line_number: int
    item: str
    quantity: float
    requested_date: date
    priority: str
    status: str
    start_date: date                       # planned start of the line's production order (or offset fallback)
    op_dates: dict[str, date] = field(default_factory=dict)  # operation number -> planned start
    has_routing: bool = True

    @property
    def sort_key(self) -> tuple:
        return (PRIORITY_RANK.get(self.priority, 9), self.requested_date, self.order_number, self.line_number)


@dataclass
class MrpInput:
    as_of: date
    horizon: tuple[date, date]
    items: dict[str, Item]
    boms: dict[str, list[BomLine]]            # approved BOM per parent item code
    supplies: dict[str, list[SupplyLot]]
    demands: list[Demand]
    holds: dict[str, list[tuple[str, float]]] = field(default_factory=dict)   # item -> [(ref, qty on QC hold)]
    undated_receipts: list[tuple[str, str, float]] = field(default_factory=list)  # (PO ref, item, open qty)
    snapshot_at: str | None = None


@dataclass(frozen=True)
class Allocation:
    ref: str
    source: str
    quantity: float
    available: date


@dataclass
class Requirement:
    """Requirement of one component for one order line (pegged), after allocation."""
    order_number: str
    line_number: int
    priority: str
    item: str
    parent: str
    level: int
    gross: float
    need_date: date
    operation_ref: str | None
    allocations: list[Allocation] = field(default_factory=list)
    planned_production: float = 0.0   # unmet quantity of a manufactured sub-assembly, exploded to its children
    shortage: float = 0.0
    ready_date: date | None = None
    lead_days: int = 0                                                    # sub-assembly build lead time
    children: list["Requirement"] = field(default_factory=list, repr=False)
    seq: int = -1                                                         # allocation order (explains decisions)

    @property
    def allocated(self) -> float:
        return sum(a.quantity for a in self.allocations)

    @property
    def status(self) -> str:
        if self.shortage > EPS:
            return "short"
        if self.ready_date and self.ready_date > self.need_date:
            return "late"
        return "covered"

    @property
    def days_late(self) -> int:
        return (self.ready_date - self.need_date).days if self.ready_date and self.ready_date > self.need_date else 0


@dataclass(frozen=True)
class LineIssue:
    order_number: str
    line_number: int
    kind: str      # missing_bom | missing_routing
    detail: str


@dataclass
class MrpResult:
    input: MrpInput
    requirements: list[Requirement]
    issues: list[LineIssue]

    def for_order(self, order_number: str) -> list[Requirement]:
        return [r for r in self.requirements if r.order_number == order_number]

    def problems(self) -> list[Requirement]:
        return [r for r in self.requirements if r.status != "covered"]

    def shortage_units(self, req: Requirement) -> float:
        item = self.input.items.get(req.item)
        return math.ceil(req.shortage - EPS) if (item is None or item.discrete) else round(req.shortage, 3)

    def short_items(self) -> list[str]:
        return sorted({r.item for r in self.requirements if r.status == "short"})

    def line_ready_date(self, order_number: str, line_number: int) -> date | None:
        """Material-ready date of an order line: None if any component is uncovered."""
        reqs = [r for r in self.requirements if (r.order_number, r.line_number) == (order_number, line_number)]
        if any(r.status == "short" for r in reqs):
            return None
        dates = [r.ready_date for r in reqs if r.ready_date]
        return max(dates) if dates else None

    @property
    def status(self) -> str:
        """Planning outcome in spec §8.7 vocabulary."""
        if self.issues:
            return "Needs data"
        if self.problems():
            return "Feasible with lateness"
        return "Feasible"
