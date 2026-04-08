from __future__ import annotations

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect

from .core import Executor, list_versions, trace_workflow
from .store import InMemoryStore, create_store


def create_app(*, workflows: list, store: InMemoryStore | None = None, redis_url: str | None = None) -> FastAPI:
    store = store or create_store(redis_url)
    executor = Executor(store=store)
    app = FastAPI(title="agentic-workgraph")
    app.state.executor = executor
    app.state.store = store
    workflow_map = {workflow.name: workflow for workflow in workflows}
    for workflow in workflows:
        store.register_workflow(workflow)

    def summarize_run(run):
        return {
            "run_id": run.run_id,
            "workflow": run.workflow,
            "version": run.version,
            "status": run.status,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "duration_ms": (
                int((run.finished_at - run.started_at).total_seconds() * 1000)
                if run.finished_at is not None
                else None
            ),
            "error_count": len(run.errors),
            "node_count": len(run.nodes),
            "llm_cost_usd": 0.0,
        }

    @app.get("/api/workflows")
    async def list_workflows():
        return [
            {
                "name": workflow.name,
                "current_version": workflow.version,
                "version_count": len(store.list_versions(workflow.name)),
                "run_count": len(store.list_runs(workflow=workflow.name)),
                "latest_run": (
                    summarize_run(
                        sorted(
                            store.list_runs(workflow=workflow.name),
                            key=lambda run: run.started_at,
                        )[-1]
                    )
                    if store.list_runs(workflow=workflow.name)
                    else None
                ),
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

    @app.get("/api/workflows/{name}/versions")
    async def get_versions(name: str):
        workflow = workflow_map.get(name)
        if workflow is None:
            raise HTTPException(status_code=404, detail="workflow not found")
        current_version = workflow.version
        versions = []
        for version in list_versions(name, store=store):
            runs = sorted(store.list_runs(workflow=name, version=version), key=lambda run: run.started_at)
            versions.append(
                {
                    "version": version,
                    "is_current": version == current_version,
                    "run_count": len(runs),
                    "latest_run": summarize_run(runs[-1]) if runs else None,
                }
            )
        return {"workflow": name, "current_version": current_version, "versions": versions}

    @app.get("/api/workflows/{name}/runs")
    async def list_workflow_runs(name: str, version: str | None = None):
        workflow = workflow_map.get(name)
        if workflow is None:
            raise HTTPException(status_code=404, detail="workflow not found")
        runs = sorted(
            store.list_runs(workflow=name, version=version),
            key=lambda run: run.started_at,
            reverse=True,
        )
        return {
            "workflow": name,
            "current_version": workflow.version,
            "version": version,
            "runs": [summarize_run(run) for run in runs],
        }

    @app.post("/api/workflows/{name}/runs")
    async def start_run(name: str, run_id: str | None = None):
        workflow = workflow_map.get(name)
        if workflow is None:
            raise HTTPException(status_code=404, detail="workflow not found")
        run = await executor.run(workflow, run_id=run_id)
        return {
            "run_id": run.run_id,
            "status": run.status,
            "workflow": run.workflow,
            "version": run.version,
        }

    @app.get("/api/runs")
    async def list_runs(workflow: str | None = None, version: str | None = None):
        runs = sorted(
            store.list_runs(workflow=workflow, version=version),
            key=lambda run: run.started_at,
            reverse=True,
        )
        return [summarize_run(run) for run in runs]

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str):
        try:
            run = store.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        return run.model_dump(mode="json")

    @app.post("/api/runs/{run_id}/resume")
    async def resume_run(run_id: str):
        try:
            run = await executor.resume(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        return {
            "run_id": run.run_id,
            "status": run.status,
            "workflow": run.workflow,
            "version": run.version,
        }

    @app.get("/api/runs/{run_id}/trace")
    async def get_run_trace(run_id: str):
        return store.get_spans(run_id)

    @app.get("/api/runs/{run_id}/timeline")
    async def get_run_timeline(run_id: str):
        try:
            run = store.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        return [
            {
                "node_id": node_id,
                "status": node.status,
                "started_at": node.started_at,
                "finished_at": node.finished_at,
                "duration_ms": node.duration_ms,
            }
            for node_id, node in run.nodes.items()
        ]

    @app.get("/api/runs/{run_id}/errors")
    async def get_run_errors(run_id: str):
        try:
            run = store.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        return [error.model_dump(mode="json") for error in run.errors]

    @app.get("/api/runs/{run_id}/nodes/{node_id}/items")
    async def list_node_items(run_id: str, node_id: str):
        try:
            run = store.get_run(run_id)
            node = run.nodes[node_id]
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run or node not found") from exc
        return [item.model_dump(mode="json") for item in node.items]

    @app.get("/api/runs/{run_id}/nodes/{node_id}/items/{index}")
    async def get_node_item(run_id: str, node_id: str, index: int):
        try:
            run = store.get_run(run_id)
            node = run.nodes[node_id]
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run or node not found") from exc
        try:
            item = node.items[index]
        except IndexError as exc:
            raise HTTPException(status_code=404, detail="item not found") from exc
        return item.model_dump(mode="json")

    @app.get("/api/runs/{run_id}/nodes/{node_id}/items/{index}/stream")
    async def get_node_item_stream(run_id: str, node_id: str, index: int):
        return store.get_stream(run_id, node_id, index)

    @app.websocket("/api/runs/{run_id}/ws")
    async def run_events(run_id: str, websocket: WebSocket):
        await websocket.accept()
        queue = store.subscribe(run_id)
        try:
            while True:
                event = await queue.get()
                await websocket.send_json(event)
                if event["event"] == "run_status" and event.get("status") in {"completed", "failed"}:
                    break
        except WebSocketDisconnect:
            pass
        finally:
            store.unsubscribe(run_id, queue)

    return app
