from __future__ import annotations

import inspect
import json
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .core import Executor, list_versions, trace_workflow
from .models import (
    GraphSpec,
    RunLaunchRequest,
    RunLaunchResponse,
    RunRecord,
    RunSummary,
    TimelineEntry,
    WorkflowRunsResponse,
    WorkflowSummary,
    WorkflowVersionEntry,
    WorkflowVersionsResponse,
)
from .store import InMemoryStore, create_store


def create_app(
    *,
    workflows: list,
    store: InMemoryStore | None = None,
    redis_url: str | None = None,
    llm_callable=None,
) -> FastAPI:
    store = store or create_store(redis_url)
    executor = Executor(store=store, llm_callable=llm_callable)
    app = FastAPI(title="agentic-workgraph")
    app.state.executor = executor
    app.state.store = store
    ui_dir = Path(__file__).resolve().parent / "ui"
    for workflow in workflows:
        store.register_workflow(workflow)

    app.mount("/ui/static", StaticFiles(directory=ui_dir), name="ui-static")

    def get_registered_workflow(name: str):
        try:
            return store.get_workflow(name)
        except KeyError:
            return None

    def get_registered_workflows() -> list:
        return sorted(store.workflows.values(), key=lambda workflow: workflow.name)

    def summarize_run(run: RunRecord) -> RunSummary:
        return RunSummary(
            run_id=run.run_id,
            workflow=run.workflow,
            version=run.version,
            status=run.status,
            started_at=run.started_at,
            finished_at=run.finished_at,
            duration_ms=(
                int((run.finished_at - run.started_at).total_seconds() * 1000)
                if run.finished_at is not None
                else None
            ),
            error_count=len(run.errors),
            node_count=len(run.nodes),
            llm_cost_usd=0.0,
        )

    @app.get("/api/workflows", response_model=list[WorkflowSummary])
    async def list_workflows():
        payload: list[WorkflowSummary] = []
        for workflow in get_registered_workflows():
            workflow_runs = store.list_runs(workflow=workflow.name)
            latest_run = (
                summarize_run(sorted(workflow_runs, key=lambda run: run.started_at)[-1])
                if workflow_runs
                else None
            )
            payload.append(
                WorkflowSummary(
                    name=workflow.name,
                    current_version=workflow.version,
                    version_count=len(store.list_versions(workflow.name)),
                    run_count=len(workflow_runs),
                    latest_run=latest_run,
                )
            )
        return payload

    @app.get("/ui")
    async def ui_index():
        return FileResponse(ui_dir / "index.html")

    @app.get("/api/workflows/{name}/graph", response_model=GraphSpec)
    async def get_graph(name: str, trace_mode: str = "auto", trace_combination_limit: int = 100):
        workflow = get_registered_workflow(name)
        if workflow is None:
            raise HTTPException(status_code=404, detail="workflow not found")
        try:
            graph, _calls = trace_workflow(
                workflow,
                trace_mode=trace_mode,
                trace_combination_limit=trace_combination_limit,
            )
        except (TypeError, ValueError) as exc:
            workflow_runs = sorted(store.list_runs(workflow=name), key=lambda run: run.started_at, reverse=True)
            if workflow_runs:
                return workflow_runs[0].graph
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return graph

    @app.get("/api/workflows/{name}/versions", response_model=WorkflowVersionsResponse)
    async def get_versions(name: str):
        workflow = get_registered_workflow(name)
        if workflow is None:
            raise HTTPException(status_code=404, detail="workflow not found")
        current_version = workflow.version
        versions: list[WorkflowVersionEntry] = []
        for version in list_versions(name, store=store):
            runs = sorted(store.list_runs(workflow=name, version=version), key=lambda run: run.started_at)
            versions.append(
                WorkflowVersionEntry(
                    version=version,
                    is_current=version == current_version,
                    run_count=len(runs),
                    latest_run=summarize_run(runs[-1]) if runs else None,
                )
            )
        return WorkflowVersionsResponse(workflow=name, current_version=current_version, versions=versions)

    @app.get("/api/workflows/{name}/launch-spec")
    async def get_launch_spec(name: str):
        workflow = get_registered_workflow(name)
        if workflow is None:
            raise HTTPException(status_code=404, detail="workflow not found")
        signature = inspect.signature(workflow.func)
        params = []
        for parameter in signature.parameters.values():
            default = None if parameter.default is inspect._empty else parameter.default
            annotation = None if parameter.annotation is inspect._empty else str(parameter.annotation)
            params.append(
                {
                    "name": parameter.name,
                    "kind": parameter.kind.name,
                    "required": parameter.default is inspect._empty,
                    "default": default,
                    "annotation": annotation,
                }
            )
        return {
            "workflow": name,
            "params": params,
        }

    @app.get("/api/workflows/{name}/runs", response_model=WorkflowRunsResponse)
    async def list_workflow_runs(name: str, version: str | None = None):
        workflow = get_registered_workflow(name)
        if workflow is None:
            raise HTTPException(status_code=404, detail="workflow not found")
        runs = sorted(
            store.list_runs(workflow=name, version=version),
            key=lambda run: run.started_at,
            reverse=True,
        )
        return WorkflowRunsResponse(
            workflow=name,
            current_version=workflow.version,
            version=version,
            runs=[summarize_run(run) for run in runs],
        )

    @app.post("/api/workflows/{name}/runs", response_model=RunLaunchResponse)
    async def start_run(name: str, request: RunLaunchRequest | None = Body(default=None), run_id: str | None = None):
        workflow = get_registered_workflow(name)
        if workflow is None:
            raise HTTPException(status_code=404, detail="workflow not found")
        request = request or RunLaunchRequest()
        run = await executor.launch(workflow, *request.args, run_id=run_id, **request.kwargs)
        return RunLaunchResponse(run_id=run.run_id, status=run.status, workflow=run.workflow, version=run.version)

    @app.get("/api/runs", response_model=list[RunSummary])
    async def list_runs(workflow: str | None = None, version: str | None = None):
        runs = sorted(
            store.list_runs(workflow=workflow, version=version),
            key=lambda run: run.started_at,
            reverse=True,
        )
        return [summarize_run(run) for run in runs]

    @app.get("/api/runs/{run_id}", response_model=RunRecord)
    async def get_run(run_id: str):
        try:
            run = store.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        return run

    @app.get("/api/runs/{run_id}/artifact")
    async def get_run_artifact(run_id: str):
        try:
            run = store.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        artifact = run.final_output[0] if run.final_output else None
        manifest = None
        manifest_path = None
        if isinstance(artifact, dict):
            manifest_path = artifact.get("manifest_path")
        elif artifact is not None:
            manifest_path = getattr(artifact, "manifest_path", None)
        if isinstance(manifest_path, str) and manifest_path:
            path = Path(manifest_path)
            if path.exists():
                try:
                    manifest = json.loads(path.read_text())
                except Exception:  # noqa: BLE001
                    manifest = None
        return {
            "run_id": run.run_id,
            "workflow": run.workflow,
            "version": run.version,
            "status": run.status,
            "artifact": artifact,
            "manifest": manifest,
        }

    @app.post("/api/runs/{run_id}/resume", response_model=RunLaunchResponse)
    async def resume_run(run_id: str):
        try:
            run = await executor.launch_resume(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        return RunLaunchResponse(run_id=run.run_id, status=run.status, workflow=run.workflow, version=run.version)

    @app.get("/api/runs/{run_id}/trace")
    async def get_run_trace(run_id: str):
        return store.get_spans(run_id)

    @app.get("/api/runs/{run_id}/timeline", response_model=list[TimelineEntry])
    async def get_run_timeline(run_id: str):
        try:
            run = store.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        return [
            TimelineEntry(
                node_id=node_id,
                status=node.status,
                started_at=node.started_at,
                finished_at=node.finished_at,
                duration_ms=node.duration_ms,
            )
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
