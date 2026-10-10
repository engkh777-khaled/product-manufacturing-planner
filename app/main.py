from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.routes import assistant, dashboard, engineering, orders, workbench

app = FastAPI(title="Product Manufacturing Planner", version="0.1.0")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

for module in (dashboard, workbench, orders, engineering, assistant):
    app.include_router(module.router)


@app.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    return RedirectResponse("/dashboard")
