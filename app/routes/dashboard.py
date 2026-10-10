from fastapi import APIRouter, Depends, Form, Request

from app.auth import require_user
from app.data.dataset import current as ds
from app.data import repository as repo
from app.models import CurrentUser, RiskLevel
from app.templating import templates

router = APIRouter(dependencies=[Depends(require_user)])


@router.get("/dashboard")
def dashboard(request: Request):
    kpis = repo.order_kpis()
    risk_orders = [v for v in repo.list_orders() if v["risk"] in (RiskLevel.HIGH, RiskLevel.MEDIUM)]
    baseline = repo.baseline()
    return templates.TemplateResponse(request, "dashboard.html", {
        "kpis": kpis,
        "risk_orders": risk_orders,
        "work_centers": repo.work_center_views(),
        "bottleneck": repo.bottleneck(),
        "shortage_skus": repo.shortage_skus(),
        "pos": repo.purchase_orders(),
        "insights": ds().COPILOT_INSIGHTS,
        "approval": next(iter(repo.approval_requests()), None),
        "baseline": baseline,
        "approver_role": ds().APPROVER_ROLE,
    })


@router.post("/approvals/{request_id}/decision")
def decide(request: Request, request_id: str, decision: str = Form(...), user: CurrentUser = Depends(require_user)):
    result = repo.request_decision(request_id, decision, user)
    return templates.TemplateResponse(request, "partials/approval_result.html", {"result": result})
