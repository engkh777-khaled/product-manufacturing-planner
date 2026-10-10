from fastapi import APIRouter, Form, Request

from app.data import mock_data as db
from app.data import repository as repo
from app.models import RiskLevel
from app.templating import templates

router = APIRouter()


@router.get("/dashboard")
def dashboard(request: Request):
    kpis = repo.order_kpis()
    risk_orders = [v for v in repo.list_orders() if v["risk"] in (RiskLevel.HIGH, RiskLevel.MEDIUM)]
    baseline = repo.get_version("v4.2")
    return templates.TemplateResponse(request, "dashboard.html", {
        "kpis": kpis,
        "risk_orders": risk_orders,
        "work_centers": repo.work_center_views(),
        "bottleneck": repo.bottleneck(),
        "shortage_skus": repo.shortage_skus(),
        "pos": repo.purchase_orders(),
        "insights": db.COPILOT_INSIGHTS,
        "approval": repo.approval_requests()[0],
        "baseline": baseline,
        "approver_role": db.APPROVER_ROLE,
    })


@router.post("/approvals/{request_id}/decision")
def decide(request: Request, request_id: str, decision: str = Form(...)):
    result = repo.request_decision(request_id, decision)
    return templates.TemplateResponse(request, "partials/approval_result.html", {"result": result})
