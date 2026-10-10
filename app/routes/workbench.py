from fastapi import APIRouter, Depends, HTTPException, Request

from app import jobs
from app.auth import require_user
from app.data import dataset
from app.data.dataset import current as ds
from app.data import repository as repo
from app.models import CurrentUser
from app.templating import templates

router = APIRouter(dependencies=[Depends(require_user)])


def _can_schedule(user: CurrentUser) -> bool:
    return dataset.is_supabase() and bool({"planner", "admin"} & set(user.roles))


@router.get("/workbench")
def workbench(request: Request, version: str | None = None, user: CurrentUser = Depends(require_user)):
    gantt = repo.gantt_view(version)
    return templates.TemplateResponse(request, "workbench.html", {
        "gantt": gantt,
        "version": gantt["version"],
        "versions": repo.schedule_versions(),
        "legend": repo.active_order_legend(),
        "cmp": repo.scenario_comparison(),
        "horizon": ds().PLANNING_HORIZON,
        "report": repo.schedule_report(gantt["version"]),
        "can_schedule": _can_schedule(user),
    })


@router.post("/workbench/run")
def run_solver(request: Request, version: str | None = None, user: CurrentUser = Depends(require_user)):
    if not _can_schedule(user):
        # Demo data (or a role without planning rights): show the recorded run, never pretend to solve.
        v = repo.get_version(version)
        repo.record_audit("Solver re-run requested", f"schedule {v.key}", user, app_event=True)
        reason = ("Only planners and admins can run the solver." if dataset.is_supabase()
                  else "Solving needs DATA_BACKEND=supabase and a planner sign-in.")
        return templates.TemplateResponse(request, "partials/solver_run.html", {"v": v, "job": None, "reason": reason})
    job = jobs.start(dataset.access_token(), user.id, user.name)
    return templates.TemplateResponse(request, "partials/solver_run.html", {"job": job})


@router.get("/workbench/runs/{job_id}")
def run_status(request: Request, job_id: str, user: CurrentUser = Depends(require_user)):
    job = jobs.get(job_id, user.id)
    if job is None:
        raise HTTPException(404, "No such solver run")
    return templates.TemplateResponse(request, "partials/solver_run.html", {"job": job})
