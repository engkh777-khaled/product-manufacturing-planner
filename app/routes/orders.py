import math
from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.auth import require_user
from app.data import dataset, writes
from app.data import repository as repo
from app.models import CurrentUser, Priority
from app.templating import templates

router = APIRouter(dependencies=[Depends(require_user)])

PAGE_SIZE = 6


def can_edit_orders(user: CurrentUser) -> bool:
    return dataset.is_supabase() and bool({"planner", "admin"} & set(user.roles))


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
           selected: str | None = None, msg: str = "", user: CurrentUser = Depends(require_user)):
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
        "can_edit": can_edit_orders(user),
        "msg": msg[:200],
    })
    return templates.TemplateResponse(request, "orders.html", ctx)


@router.get("/orders/table")
def orders_table(request: Request, tab: str = "all", q: str = "", priority: str = "", page: int = 1,
                 selected: str | None = None):
    return templates.TemplateResponse(request, "partials/orders_table.html",
                                      _table_context(tab, q, priority, page, selected))


@router.get("/orders/{order_number}/inspector")
def order_inspector(request: Request, order_number: str, user: CurrentUser = Depends(require_user)):
    detail = repo.get_order(order_number)
    if detail is None:
        raise HTTPException(404, f"Order {order_number} not found")
    return templates.TemplateResponse(request, "partials/order_inspector.html",
                                      {"detail": detail, "can_edit": can_edit_orders(user)})


# ------------------------------------------------------------------ create / edit drafts (M06)

def _form_page(request: Request, order: dict, errors: list[str], is_new: bool, status: int = 200):
    return templates.TemplateResponse(request, "order_form.html", {
        "order": order, "errors": errors, "is_new": is_new, "products": repo.orderable_products(),
        "priorities": [p.value for p in Priority]}, status_code=status)


def _require_editor(user: CurrentUser) -> None:
    if not can_edit_orders(user):
        raise HTTPException(403, "Editing orders requires DATA_BACKEND=supabase and the planner or admin role.")


@router.get("/orders/new")
def new_order(request: Request, user: CurrentUser = Depends(require_user)):
    _require_editor(user)
    today = date.today().isoformat()
    order = {"order_number": repo.next_order_number(), "customer_name": "", "customer_reference": "", "order_date": today,
             "requested_delivery_date": "", "priority": "Normal", "notes": "",
             "lines": [{"product_code": "", "quantity": "", "unit_price": "", "tags": "", "notes": ""}]}
    return _form_page(request, order, [], is_new=True)


@router.get("/orders/{order_number}/edit")
def edit_order(request: Request, order_number: str, user: CurrentUser = Depends(require_user)):
    _require_editor(user)
    order = repo.order_for_edit(order_number)
    if order is None:
        raise HTTPException(404, f"Order {order_number} not found")
    if order["status"] != "Draft":
        return RedirectResponse(f"/orders?selected={order_number}&msg={quote('Only Draft orders can be edited.')}", 303)
    return _form_page(request, order, [], is_new=False)


def _parse_order(form) -> tuple[dict, list[str]]:
    """Server-side validation (the database re-checks everything)."""
    errors: list[str] = []
    order = {k: (form.get(k) or "").strip() for k in
             ("order_number", "customer_name", "customer_reference", "order_date", "requested_delivery_date", "priority", "notes")}
    products, quantities = form.getlist("product_code"), form.getlist("quantity")
    prices, tags, notes = form.getlist("unit_price"), form.getlist("tags"), form.getlist("line_notes")
    allowed = {p["code"] for p in repo.orderable_products()}
    lines = []
    for i, code in enumerate(products):
        qty = (quantities[i] if i < len(quantities) else "").strip()
        if not code and not qty:
            continue  # empty row
        line = {"product_code": code, "quantity": qty, "unit_price": (prices[i] if i < len(prices) else "").strip(),
                "tags": (tags[i] if i < len(tags) else "").strip()[:120], "notes": (notes[i] if i < len(notes) else "").strip()[:200]}
        if code not in allowed:
            errors.append(f"Line {len(lines) + 1}: choose an active product.")
        try:
            if float(qty) <= 0 or float(qty) != int(float(qty)):
                raise ValueError
        except ValueError:
            errors.append(f"Line {len(lines) + 1}: quantity must be a whole number above zero.")
        if line["unit_price"]:
            try:
                if float(line["unit_price"]) < 0:
                    raise ValueError
            except ValueError:
                errors.append(f"Line {len(lines) + 1}: unit price must be zero or more.")
        lines.append(line)
    order["lines"] = lines or [{"product_code": "", "quantity": "", "unit_price": "", "tags": "", "notes": ""}]
    if not order["customer_name"]:
        errors.append("Customer is required.")
    if order["priority"] not in [p.value for p in Priority]:
        errors.append("Choose a priority.")
    try:
        od, rd = date.fromisoformat(order["order_date"]), date.fromisoformat(order["requested_delivery_date"])
        if rd < od:
            errors.append("Requested delivery date cannot be before the order date.")
    except ValueError:
        errors.append("Order date and requested delivery date are required.")
    if not lines:
        errors.append("Add at least one line.")
    return order, errors


@router.post("/orders/save")
async def save_order(request: Request, user: CurrentUser = Depends(require_user)):
    _require_editor(user)
    form = await request.form()
    order, errors = _parse_order(form)
    is_new = repo.get_order(order["order_number"]) is None
    if errors:
        return _form_page(request, order, errors, is_new, status=422)
    result = writes.save_draft_order(order)
    if not result.ok:
        return _form_page(request, order, [result.message], is_new, status=422)
    repo.record_audit("Sales order saved (Draft)", order["order_number"], user)
    return RedirectResponse(f"/orders?selected={order['order_number']}&msg={quote(result.message)}", status_code=303)


@router.post("/orders/{order_number}/confirm")
def confirm(order_number: str, user: CurrentUser = Depends(require_user)):
    _require_editor(user)
    result = writes.confirm_order(order_number)
    if result.ok:
        repo.record_audit("Sales order confirmed", order_number, user)
    return RedirectResponse(f"/orders?selected={order_number}&msg={quote(result.message)}", status_code=303)


@router.post("/orders/{order_number}/delete")
def delete(order_number: str, user: CurrentUser = Depends(require_user)):
    _require_editor(user)
    result = writes.delete_draft_order(order_number)
    if result.ok:
        repo.record_audit("Draft sales order deleted", order_number, user)
        return RedirectResponse(f"/orders?msg={quote(result.message)}", status_code=303)
    return RedirectResponse(f"/orders?selected={order_number}&msg={quote(result.message)}", status_code=303)
