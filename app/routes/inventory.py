"""Inventory & Purchasing (spec §12.4, M06)."""

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request

from app.auth import require_user
from app.data import dataset, writes
from app.data import repository as repo
from app.data.dataset import current as ds
from app.models import CurrentUser
from app.templating import templates

router = APIRouter(dependencies=[Depends(require_user)])

PO_LINE_STATUSES = ["Open", "In Transit", "Partially Received", "Received", "Cancelled"]
QUALITY_STATUSES = ["Not Inspected", "QC Hold", "Released", "Rejected"]


def _roles(user: CurrentUser) -> set[str]:
    return set(user.roles) if dataset.is_supabase() else set()


def _context(user: CurrentUser) -> dict:
    roles = _roles(user)
    inventory = ds().INVENTORY
    lines = ds().PO_LINES
    return {
        "inventory": inventory,
        "po_lines": lines,
        "shortages": repo.shortage_skus(),
        "late": [p for p in repo.purchase_orders() if p["slip"] and p["slip"] > 0],
        "can_edit_stock": bool({"stores", "admin"} & roles),
        "can_edit_po": bool({"purchasing", "stores", "admin"} & roles),
        "statuses": PO_LINE_STATUSES,
        "qualities": QUALITY_STATUSES,
        "totals": {
            "skus": len({r["product_code"] for r in inventory}),
            "on_hold": sum(r["quality_hold"] for r in inventory),
            "open_lines": sum(1 for r in lines if r["status"] not in ("Received", "Cancelled")),
        },
        "editable_backend": dataset.is_supabase(),
    }


@router.get("/inventory")
def inventory_page(request: Request, user: CurrentUser = Depends(require_user)):
    return templates.TemplateResponse(request, "inventory.html", _context(user))


def _row(rows: list[dict], row_id: str) -> dict:
    row = next((r for r in rows if r["id"] == row_id), None)
    if row is None:
        raise HTTPException(404, "Row not found")
    return row


@router.post("/inventory/balances/{balance_id}")
def update_balance(request: Request, balance_id: str, on_hand: float = Form(...), reserved: float = Form(0),
                   quality_hold: float = Form(0), user: CurrentUser = Depends(require_user)):
    row = dict(_row(ds().INVENTORY, balance_id))
    if min(on_hand, reserved, quality_hold) < 0 or reserved + quality_hold > on_hand:
        message, ok = "Quantities must be ≥ 0 and reserved + QC hold cannot exceed on hand.", False
    else:
        result = writes.update_inventory(balance_id, on_hand, reserved, quality_hold)
        message, ok = result.message, result.ok
        if ok:
            repo.record_audit("Inventory adjusted", f"{row['product_code']} {row['warehouse_code']} {row['lot_number']}".strip(), user,
                              f"on hand {row['on_hand']:g}→{on_hand:g}, reserved {row['reserved']:g}→{reserved:g}, "
                              f"hold {row['quality_hold']:g}→{quality_hold:g}")
            row.update(on_hand=on_hand, reserved=reserved, quality_hold=quality_hold,
                       net_available=on_hand - reserved - quality_hold)
    return templates.TemplateResponse(request, "partials/inventory_row.html",
                                      {"r": row, "can_edit_stock": True, "message": message, "ok": ok})


@router.post("/inventory/po-lines/{line_id}")
def update_po_line(request: Request, line_id: str, expected: str = Form(""), received: float = Form(0),
                   status: str = Form(...), quality_status: str = Form(...), user: CurrentUser = Depends(require_user)):
    row = dict(_row(ds().PO_LINES, line_id))
    try:
        expected_date = date.fromisoformat(expected) if expected else None
    except ValueError:
        expected_date = "invalid"
    if expected_date == "invalid" or status not in PO_LINE_STATUSES or quality_status not in QUALITY_STATUSES:
        message, ok = "Check the date and statuses.", False
    elif received < 0 or received > row["ordered"]:
        message, ok = f"Received must be between 0 and the ordered quantity ({row['ordered']:g}).", False
    else:
        result = writes.update_po_line(line_id, {
            "expected_delivery_date": expected_date.isoformat() if expected_date else None,
            "received_quantity": received, "status": status, "quality_status": quality_status})
        message, ok = result.message, result.ok
        if ok:
            repo.record_audit("PO line updated", f"{row['po_number']}/{row['line_number']}", user,
                              f"ETA {row['expected']}→{expected_date}, received {row['received']:g}→{received:g}, "
                              f"{row['status']}→{status}, quality {row['quality_status']}→{quality_status}")
            row.update(expected=expected_date, received=received, open=row["ordered"] - received,
                       status=status, quality_status=quality_status)
    return templates.TemplateResponse(request, "partials/po_line_row.html", {
        "l": row, "can_edit_po": True, "statuses": PO_LINE_STATUSES, "qualities": QUALITY_STATUSES,
        "message": message, "ok": ok})
