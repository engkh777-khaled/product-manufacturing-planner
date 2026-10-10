from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.auth import require_user
from app.data import dataset, writes
from app.data.dataset import current as ds
from app.models import CurrentUser
from app.data import repository as repo
from app.templating import templates

router = APIRouter(dependencies=[Depends(require_user)])


def _check_product(code: str) -> None:
    if code not in ds().BOMS:
        raise HTTPException(404, f"No BOM for product {code}")


@router.get("/engineering")
def engineering(request: Request, product: str = "V-600-TBV-10", msg: str = "",
                user: CurrentUser = Depends(require_user)):
    view = repo.bom_view(product)
    return templates.TemplateResponse(request, "engineering.html", {
        "v": view,
        "products": repo.products(),
        "machines": repo.machine_index(),
        "work_centers": repo.work_center_views(),
        "revisions": getattr(ds(), "BOM_REVISIONS", {}).get(view["product"].product_code, []),
        "can_engineer": dataset.is_supabase() and bool({"engineering", "admin"} & set(user.roles)),
        "can_approve": dataset.is_supabase() and "approver" in user.roles,
        "msg": msg[:200],
    })


@router.get("/engineering/{product_code}/bom")
def bom_table(request: Request, product_code: str, scrap_delta: float = 0.0):
    _check_product(product_code)
    scrap_delta = min(max(scrap_delta, 0.0), 0.5)
    return templates.TemplateResponse(request, "partials/bom_table.html",
                                      {"v": repo.bom_view(product_code, scrap_delta)})


@router.post("/engineering/{product_code}/validate")
def validate(request: Request, product_code: str):
    _check_product(product_code)
    checks = repo.validate_bom(product_code)
    return templates.TemplateResponse(request, "partials/bom_validation.html", {"checks": checks})


# ------------------------------------------------------------------ BOM revision workflow (M06)
# Draft --(engineering)--> Under Review --(approver)--> Approved (previous Approved → Superseded)
#                                        --(approver/engineering)--> back to Draft

ACTIONS = {
    "submit": (writes.submit_bom_for_review, "BOM revision submitted for review"),
    "return": (writes.return_bom_to_draft, "BOM revision returned to draft"),
    "approve": (writes.approve_bom, "BOM revision approved"),
}


@router.post("/engineering/revisions/{bom_id}/{action}")
def revision_action(bom_id: str, action: str, product: str, user: CurrentUser = Depends(require_user)):
    if action not in ACTIONS or not dataset.is_supabase():
        raise HTTPException(404, "Unknown action")
    func, audit_action = ACTIONS[action]
    result = func(bom_id)
    if result.ok:
        repo.record_audit(audit_action, f"BOM {product} ({bom_id})", user)
    return RedirectResponse(f"/engineering?product={quote(product)}&msg={quote(result.message)}", status_code=303)
