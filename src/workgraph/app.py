from __future__ import annotations

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect

from .core import Executor, list_versions, trace_workflow
from .store import InMemoryStore


def create_app(*, workflows: list, store: InMemoryStore | None = None) -> FastAPI:
    store = store or InMemoryStore()
    executor = Executor(store=store)
    app = FastAPI(title="agentic-workgraph")
    workflow_map = {workflow.name: workflow for workflow in workflows}
    for workflow in workflows:
        store.register_workflow(workflow)

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

    @app.get("/api/workflows/{name}/versions")
    async def get_versions(name: str):
        workflow = workflow_map.get(name)
        if workflow is None:
            raise HTTPException(status_code=404, detail="workflow not found")
        return {"workflow": name, "versions": list_versions(name, store=store)}

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
    async def list_runs():
        return [run.model_dump(mode="json") for run in store.list_runs()]

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str):
        try:
            run = store.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        return run.model_dump(mode="json")

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
