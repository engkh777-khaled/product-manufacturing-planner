from fastapi import APIRouter, Depends, Request

from app.auth import require_user
from app.data.dataset import current as ds
from app.data import repository as repo
from app.models import CurrentUser
from app.templating import templates

router = APIRouter(dependencies=[Depends(require_user)])


@router.get("/workbench")
def workbench(request: Request, version: str | None = None):
    gantt = repo.gantt_view(version)
    return templates.TemplateResponse(request, "workbench.html", {
        "gantt": gantt,
        "version": gantt["version"],
        "versions": repo.schedule_versions(),
        "legend": repo.active_order_legend(),
        "cmp": repo.scenario_comparison(),
        "horizon": ds().PLANNING_HORIZON,
    })


@router.post("/workbench/run")
def run_solver(request: Request, version: str | None = None, user: CurrentUser = Depends(require_user)):
    v = repo.get_version(version)
    repo.record_audit("Solver re-run requested", f"schedule {v.key}", user)
    return templates.TemplateResponse(request, "partials/solver_run.html", {"v": v})
