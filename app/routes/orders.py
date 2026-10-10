import math

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import require_user
from app.data import repository as repo
from app.models import Priority
from app.templating import templates

router = APIRouter(dependencies=[Depends(require_user)])

PAGE_SIZE = 6


def _table_context(tab: str, q: str, priority: str, page: int, selected: str | None) -> dict:
    rows = repo.list_orders(tab, q, priority)
    pages = max(1, math.ceil(len(rows) / PAGE_SIZE))
    page = min(max(page, 1), pages)
    return {
        "rows": rows[(page - 1) * PAGE_SIZE: page * PAGE_SIZE],
        "total": len(rows),
        "page": page,
        "pages": pages,
        "tab": tab,
        "q": q,
        "priority": priority,
        "selected": selected,
    }


@router.get("/orders")
def orders(request: Request, tab: str = "all", q: str = "", priority: str = "", page: int = 1,
           selected: str | None = None):
    ctx = _table_context(tab, q, priority, page, selected)
    if selected is None and ctx["rows"]:
        selected = ctx["rows"][0]["o"].order_number
        ctx["selected"] = selected
    ctx.update({
        "kpis": repo.order_kpis(),
        "tabs": repo.ORDER_TABS,
        "counts": repo.tab_counts(),
        "priorities": [p.value for p in Priority],
        "detail": repo.get_order(selected) if selected else None,
    })
    return templates.TemplateResponse(request, "orders.html", ctx)


@router.get("/orders/table")
def orders_table(request: Request, tab: str = "all", q: str = "", priority: str = "", page: int = 1,
                 selected: str | None = None):
    return templates.TemplateResponse(request, "partials/orders_table.html",
                                      _table_context(tab, q, priority, page, selected))


@router.get("/orders/{order_number}/inspector")
def order_inspector(request: Request, order_number: str):
    detail = repo.get_order(order_number)
    if detail is None:
        raise HTTPException(404, f"Order {order_number} not found")
    return templates.TemplateResponse(request, "partials/order_inspector.html", {"detail": detail})
