from fastapi import APIRouter, Request

from app.data import mock_data as db
from app.data import repository as repo
from app.templating import templates

router = APIRouter()


@router.get("/workbench")
def workbench(request: Request, version: str = "v4.2"):
    gantt = repo.gantt_view(version)
    return templates.TemplateResponse(request, "workbench.html", {
        "gantt": gantt,
        "version": gantt["version"],
        "versions": repo.schedule_versions(),
        "legend": repo.active_order_legend(),
        "cmp": repo.scenario_comparison(),
        "horizon": db.PLANNING_HORIZON,
    })


@router.post("/workbench/run")
def run_solver(request: Request, version: str = "v4.2"):
    v = repo.get_version(version)
    repo.record_audit("Solver re-run requested", f"schedule {v.key}")
    return templates.TemplateResponse(request, "partials/solver_run.html", {"v": v})
