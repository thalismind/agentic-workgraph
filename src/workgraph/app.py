from __future__ import annotations

from fastapi import FastAPI, HTTPException

from .core import Executor, trace_workflow
from .store import InMemoryStore


def create_app(*, workflows: list, store: InMemoryStore | None = None) -> FastAPI:
    store = store or InMemoryStore()
    executor = Executor(store=store)
    app = FastAPI(title="agentic-workgraph")
    workflow_map = {workflow.name: workflow for workflow in workflows}

    @app.get("/api/workflows")
    async def list_workflows():
        return [
            {
                "name": workflow.name,
                "version": workflow.version,
            }
            for workflow in workflows
        ]

    @app.get("/api/workflows/{name}/graph")
    async def get_graph(name: str):
        workflow = workflow_map.get(name)
        if workflow is None:
            raise HTTPException(status_code=404, detail="workflow not found")
        graph, _calls = trace_workflow(workflow)
        return graph.model_dump(by_alias=True)

    @app.post("/api/workflows/{name}/runs")
    async def start_run(name: str):
        workflow = workflow_map.get(name)
        if workflow is None:
            raise HTTPException(status_code=404, detail="workflow not found")
        run = await executor.run(workflow)
        return {
            "run_id": run.run_id,
            "status": run.status,
            "workflow": run.workflow,
            "version": run.version,
        }

    @app.get("/api/runs")
    async def list_runs():
        return [run.model_dump(mode="json") for run in store.list_runs()]

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str):
        try:
            run = store.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        return run.model_dump(mode="json")

    return app
