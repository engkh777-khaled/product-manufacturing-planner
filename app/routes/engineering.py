from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth import require_user
from app.data import mock_data as db
from app.data import repository as repo
from app.templating import templates

router = APIRouter(dependencies=[Depends(require_user)])


def _check_product(code: str) -> None:
    if code not in db.BOMS:
        raise HTTPException(404, f"No BOM for product {code}")


@router.get("/engineering")
def engineering(request: Request, product: str = "V-600-TBV-10"):
    view = repo.bom_view(product)
    return templates.TemplateResponse(request, "engineering.html", {
        "v": view,
        "products": repo.products(),
        "machines": repo.machine_index(),
        "work_centers": repo.work_center_views(),
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
