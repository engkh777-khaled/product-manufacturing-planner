from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.data import mock_data as db
from app.data import repository as repo

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

NAV = [
    ("/dashboard", "dashboard", "Executive Dashboard"),
    ("/workbench", "view_timeline", "Planning Workbench"),
    ("/orders", "receipt_long", "Sales Orders & Demand"),
    ("/engineering", "schema", "Engineering & BOM"),
    ("/assistant", "psychology", "AI Planning Assistant"),
]


def _d(value: date | datetime | None, fmt: str = "%b %d") -> str:
    return "—" if value is None else value.strftime(fmt)


def _money(value: float) -> str:
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value / 1_000:.0f}K"
    return f"${value:,.0f}"


def _num(value: float, digits: int = 0) -> str:
    return f"{value:,.{digits}f}"


templates.env.filters.update(d=_d, money=_money, num=_num)


def shell_context() -> dict:
    """Globals for the header / sidebar."""
    b = repo.bottleneck()
    kpis = repo.order_kpis()
    active_machines = sum(1 for wc in db.WORK_CENTERS for m in wc.machines if not m.down)
    total_machines = sum(len(wc.machines) for wc in db.WORK_CENTERS)
    baseline = repo.get_version("v4.2")
    return {
        "nav": NAV,
        "plant": db.PLANT,
        "user": db.CURRENT_USER,
        "solver_label": f"CP-SAT Solver: {baseline.solver_status.replace('_', ' ').title()} (Run #{baseline.run_number})",
        "alert_count": len(repo.alerts()),
        "telemetry": [
            ("Active Machines", f"{active_machines} / {total_machines}", "text-on-surface"),
            ("Critical Shortages", f"{len(repo.shortage_skus())} SKUs", "text-crit"),
            ("OTD Forecast", f"{kpis['otd_pct']}%", "text-tertiary"),
            ("Bottleneck", b["wc"].code, "text-warn"),
        ],
    }


templates.env.globals["shell"] = shell_context
