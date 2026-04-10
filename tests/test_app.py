from __future__ import annotations

import asyncio
import time
from enum import Enum
from typing import Annotated, Literal

from fastapi.testclient import TestClient
from pydantic import Field

from workgraph import create_app, node, run_subgraph, workflow
from workgraph.testing import MockLLM
from workgraph.store import InMemoryStore


def wait_for_run_status(client: TestClient, run_id: str, expected: str, timeout: float = 2.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(f"/api/runs/{run_id}")
        if response.status_code == 200:
            payload = response.json()
            if payload["status"] == expected:
                return payload
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} did not reach status {expected}")


@node(id="hello")
async def hello(ctx, name: str):
    return f"hello {name}"


@workflow(name="hello-flow")
def hello_flow():
    return hello(name=["world"])


def test_app_exposes_workflow_graph():
    app = create_app(workflows=[hello_flow])
    client = TestClient(app)

    ui = client.get("/ui")
    assert ui.status_code == 200
    assert "Agentic Workgraph" in ui.text
    assert "Workflow History" in ui.text
    assert "/ui/static/vendor/litegraph.css" in ui.text
    assert "/ui/static/vendor/litegraph.js" in ui.text
    assert "graph-warnings" in ui.text
    assert "run-workflow-button" in ui.text
    assert "run-workflow-menu-button" in ui.text
    assert "run-workflow-menu" in ui.text
    assert "run-workflow-args" in ui.text
    assert "run-workflow-kwargs" in ui.text
    assert "focus-debugger-button" in ui.text
    assert "restore-layout-button" in ui.text
    assert "ws-status-indicator" in ui.text
    assert "ws-status-label" in ui.text
    assert "ws-status-last" in ui.text
    assert "toggle-final-artifact" in ui.text
    assert "toggle-items-list" in ui.text
    assert "toggle-trace-list" in ui.text

    ui_script = client.get("/ui/static/app.js")
    assert ui_script.status_code == 200
    assert './graph.js' in ui_script.text
    assert './router.js' in ui_script.text
    assert './state.js' in ui_script.text
    assert "loadNodeInspector" in ui_script.text
    assert "scheduleTraceRefresh" in ui_script.text
    assert "applyEvent" in ui_script.text
    assert "launchWorkflowRun" in ui_script.text
    assert "launchWorkflowRunFromMenu" in ui_script.text
    assert "launchWorkflowRunWithPayload" in ui_script.text
    assert "parseJsonField" in ui_script.text
    assert "setDetailFocus" in ui_script.text
    assert "renderLayoutControls" in ui_script.text
    assert "renderWsStatus" in ui_script.text
    assert "startWsStatusClock" in ui_script.text
    assert "toggleSection" in ui_script.text
    assert "renderCollapsedSections" in ui_script.text
    assert "buildArtifactPreview" in ui_script.text
    assert "_hidden_context_fields" in ui_script.text
    assert '"hashchange"' in ui_script.text

    graph_script = client.get("/ui/static/graph.js")
    assert graph_script.status_code == 200
    assert "LiteGraph" in graph_script.text
    assert "LGraphCanvas" in graph_script.text
    assert "computeGraphLayout" in graph_script.text
    assert "renderGraph" in graph_script.text
    assert "streamingNodes" in graph_script.text

    litegraph_script = client.get("/ui/static/vendor/litegraph.js")
    assert litegraph_script.status_code == 200
    assert "LiteGraph" in litegraph_script.text

    litegraph_css = client.get("/ui/static/vendor/litegraph.css")
    assert litegraph_css.status_code == 200
    assert ".litegraph" in litegraph_css.text

    router_script = client.get("/ui/static/router.js")
    assert router_script.status_code == 200
    assert "parseHashRoute" in router_script.text
    assert "window.location.hash" in router_script.text

    response = client.get("/api/workflows")
    assert response.status_code == 200
    assert response.json()[0]["name"] == "hello-flow"
    assert response.json()[0]["current_version"] == hello_flow.version
    assert response.json()[0]["run_count"] == 0
    assert response.json()[0]["latest_run"] is None

    graph = client.get("/api/workflows/hello-flow/graph")
    assert graph.status_code == 200
    assert graph.json()["nodes"][0]["node_id"] == "hello"

    versions = client.get("/api/workflows/hello-flow/versions")
    assert versions.status_code == 200
    assert versions.json()["current_version"] == hello_flow.version
    assert versions.json()["versions"] == [
        {
            "version": hello_flow.version,
            "is_current": True,
            "run_count": 0,
            "latest_run": None,
        }
    ]

    launch_spec = client.get("/api/workflows/hello-flow/launch-spec")
    assert launch_spec.status_code == 200
    assert launch_spec.json()["workflow"] == "hello-flow"
    assert launch_spec.json()["params"] == []

    run = client.post("/api/workflows/hello-flow/runs")
    assert run.status_code == 200
    run_id = run.json()["run_id"]
    assert run.json()["status"] == "pending"
    wait_for_run_status(client, run_id, "completed")

    items = client.get(f"/api/runs/{run_id}/nodes/hello_0/items")
    assert items.status_code == 200
    item_payload = items.json()[0]
    assert item_payload["attempts"] == 1
    assert item_payload["errors"] == []
    assert item_payload["index"] == 0
    assert item_payload["input"] == "world"
    assert item_payload["output"] == "hello world"
    assert item_payload["progress"] == 1.0
    assert item_payload["status"] == "completed"
    assert item_payload["started_at"] is not None
    assert item_payload["finished_at"] is not None

    item = client.get(f"/api/runs/{run_id}/nodes/hello_0/items/0")
    assert item.status_code == 200
    assert item.json()["output"] == "hello world"

    errors = client.get(f"/api/runs/{run_id}/errors")
    assert errors.status_code == 200
    assert errors.json() == []

    artifact = client.get(f"/api/runs/{run_id}/artifact")
    assert artifact.status_code == 200
    assert artifact.json()["run_id"] == run_id
    assert artifact.json()["artifact"] == "hello world"
    assert artifact.json()["manifest"] is None


@node(id="slow_hello")
async def slow_hello(ctx, name: str):
    await asyncio.sleep(0.01)
    return f"slow hello {name}"


@workflow(name="slow-flow")
def slow_flow():
    return slow_hello(name=["world"])


@node(id="stream_hello")
async def stream_hello(ctx, name: str):
    return await ctx.llm(prompt=f"hello {name}")


@workflow(name="stream-flow")
def stream_flow():
    return stream_hello(name=["world"])


@node(id="select_greeting")
async def select_greeting(ctx, name: str):
    return f"hello {name}"


@workflow(name="parameter-flow")
def parameter_flow(name: list[str] | None = None):
    return select_greeting(name=name or ["default"])


class GraphMode(str, Enum):
    BASIC = "basic"
    ADVANCED = "advanced"


@node(id="graph_seed")
async def graph_seed(ctx, value: str):
    return value


@node(id="graph_enum")
async def graph_enum(ctx, value: str):
    return value


@node(id="graph_literal")
async def graph_literal(ctx, value: str):
    return value


@node(id="graph_numeric")
async def graph_numeric(ctx, value: str):
    return value


@node(id="graph_combo")
async def graph_combo(ctx, value: str):
    return value


@workflow(name="graph-parameter-flow")
def graph_parameter_flow(
    mode: GraphMode,
    detail: Literal["short", "long"],
    count: Annotated[int, Field(ge=1, le=3)],
):
    value = graph_seed(value=[f"{mode.value}:{detail}:{count}"])
    if mode is GraphMode.ADVANCED:
        value = graph_enum(value=value)
    if detail == "long":
        value = graph_literal(value=value)
    if count >= 2:
        value = graph_numeric(value=value)
    if mode is GraphMode.ADVANCED and detail == "long" and count == 3:
        value = graph_combo(value=value)
    return value


def test_run_emits_event_history():
    store = InMemoryStore()
    app = create_app(workflows=[slow_flow], store=store)
    client = TestClient(app)
    run_id = "run-ws-test"
    response = client.post(f"/api/workflows/slow-flow/runs?run_id={run_id}")
    assert response.status_code == 200
    wait_for_run_status(client, run_id, "completed")
    events = store.event_history[run_id]

    event_names = [event["event"] for event in events]
    assert "run_status" in event_names
    assert "node_status" in event_names
    assert "node_output" in event_names
    assert events[-1]["event"] == "run_status"
    assert events[-1]["status"] == "completed"


def test_stream_events_and_recording():
    mock = MockLLM()
    mock.on("stream_hello").stream(["slow ", "hello ", "world"], "slow hello world")
    store = InMemoryStore()
    app = create_app(workflows=[stream_flow], store=store)
    app.state.executor.llm_callable = mock
    client = TestClient(app)

    response = client.post("/api/workflows/stream-flow/runs?run_id=stream-run")
    assert response.status_code == 200
    wait_for_run_status(client, "stream-run", "completed")

    events = store.event_history["stream-run"]
    stream_events = [event for event in events if event["event"] == "node_stream"]
    assert [event["token"] for event in stream_events] == ["slow ", "hello ", "world"]
    assert any(event["event"] == "node_stream_end" for event in events)
    node_output = next(event for event in events if event["event"] == "node_output")
    assert node_output["output"] == ["slow hello world"]

    stream = client.get("/api/runs/stream-run/nodes/stream_hello_0/items/0/stream")
    assert stream.status_code == 200
    assert [entry["token"] for entry in stream.json()] == ["slow ", "hello ", "world"]

    trace = client.get("/api/runs/stream-run/trace")
    assert trace.status_code == 200
    spans = trace.json()
    span_names = [span["name"] for span in spans]
    assert "stream-flow" in span_names
    assert "stream_hello" in span_names
    assert "llm.complete" in span_names
    llm_span = next(span for span in spans if span["name"] == "llm.complete")
    assert llm_span["attributes"]["workgraph.run.id"] == "stream-run"
    assert llm_span["attributes"]["workgraph.node.instance_id"] == "stream_hello_0"
    assert llm_span["attributes"]["workgraph.validation.feedback_applied"] is False
    assert llm_span["attributes"]["llm.tokens.input"] >= 2
    assert llm_span["attributes"]["llm.tokens.output"] >= 3
    assert llm_span["attributes"]["llm.cost.usd"] == 0.0
    assert "llm.latency_ms" in llm_span["attributes"]

    node_span = next(span for span in spans if span["name"] == "stream_hello")
    assert node_span["attributes"]["workgraph.validation.passed"] is True
    assert node_span["attributes"]["workgraph.validation.strategy"] == "retry"

    timeline = client.get("/api/runs/stream-run/timeline")
    assert timeline.status_code == 200
    row = next(item for item in timeline.json() if item["node_id"] == "stream_hello_0")
    assert row["duration_ms"] is not None
    assert row["started_at"] is not None
    assert row["finished_at"] is not None


def test_run_launch_accepts_workflow_kwargs():
    app = create_app(workflows=[parameter_flow])
    client = TestClient(app)

    response = client.post("/api/workflows/parameter-flow/runs", json={"kwargs": {"name": ["custom"]}})
    assert response.status_code == 200
    run_id = response.json()["run_id"]
    payload = wait_for_run_status(client, run_id, "completed")
    assert payload["workflow_kwargs"] == {"name": ["custom"]}

    item = client.get(f"/api/runs/{run_id}/nodes/select_greeting_0/items/0")
    assert item.status_code == 200
    assert item.json()["input"] == "custom"
    assert item.json()["output"] == "hello custom"

    positional = client.post("/api/workflows/parameter-flow/runs", json={"args": [["positional"]]})
    assert positional.status_code == 200

    positional_run_id = positional.json()["run_id"]
    positional_payload = wait_for_run_status(client, positional_run_id, "completed")
    assert positional_payload["workflow_args"] == [["positional"]]
    assert positional_payload["workflow_kwargs"] == {}

    positional_item = client.get(f"/api/runs/{positional_run_id}/nodes/select_greeting_0/items/0")
    assert positional_item.status_code == 200
    assert positional_item.json()["input"] == "positional"
    assert positional_item.json()["output"] == "hello positional"

    launch_spec = client.get("/api/workflows/parameter-flow/launch-spec")
    assert launch_spec.status_code == 200
    assert launch_spec.json()["params"][0]["name"] == "name"


def test_graph_endpoint_supports_trace_modes_for_parameterized_workflows():
    app = create_app(workflows=[graph_parameter_flow])
    client = TestClient(app)

    simple_graph = client.get("/api/workflows/graph-parameter-flow/graph?trace_mode=simple")
    assert simple_graph.status_code == 200
    assert "graph_combo" not in [node["node_id"] for node in simple_graph.json()["nodes"]]

    combined_graph = client.get("/api/workflows/graph-parameter-flow/graph?trace_mode=combined")
    assert combined_graph.status_code == 200
    assert "graph_combo" in [node["node_id"] for node in combined_graph.json()["nodes"]]

    invalid = client.get("/api/workflows/graph-parameter-flow/graph?trace_mode=bogus")
    assert invalid.status_code == 400


def test_run_artifact_reads_manifest(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text('{"next_inputs":{"selector":"example.json","downstream_graph":"next-graph"}}')

    @node(id="artifact_node")
    async def artifact_node(ctx, value: str):
        return {
            "graph_name": "demo-graph",
            "run_name": value,
            "run_dir": str(tmp_path),
            "manifest_path": str(manifest_path),
            "status": "approved",
            "summary": "demo",
        }

    @workflow(name="artifact-flow")
    def artifact_flow():
        return artifact_node(value=["demo"])

    app = create_app(workflows=[artifact_flow])
    client = TestClient(app)
    response = client.post("/api/workflows/artifact-flow/runs")
    assert response.status_code == 200
    run_id = response.json()["run_id"]
    wait_for_run_status(client, run_id, "completed")

    artifact = client.get(f"/api/runs/{run_id}/artifact")
    assert artifact.status_code == 200
    payload = artifact.json()
    assert payload["artifact"]["graph_name"] == "demo-graph"
    assert payload["manifest"]["next_inputs"]["downstream_graph"] == "next-graph"


def test_run_history_filters_by_workflow_and_version():
    app = create_app(workflows=[hello_flow, stream_flow])
    client = TestClient(app)

    run_a = client.post("/api/workflows/hello-flow/runs?run_id=hello-run")
    run_b = client.post("/api/workflows/stream-flow/runs?run_id=stream-run-2")

    assert run_a.status_code == 200
    assert run_b.status_code == 200
    wait_for_run_status(client, "hello-run", "completed")
    wait_for_run_status(client, "stream-run-2", "failed")

    all_runs = client.get("/api/runs")
    assert all_runs.status_code == 200
    assert {run["run_id"] for run in all_runs.json()} >= {"hello-run", "stream-run-2"}
    assert all("duration_ms" in run for run in all_runs.json())
    assert all("error_count" in run for run in all_runs.json())

    hello_runs = client.get(f"/api/runs?workflow=hello-flow&version={hello_flow.version}")
    assert hello_runs.status_code == 200
    assert [run["run_id"] for run in hello_runs.json()] == ["hello-run"]

    workflow_runs = client.get(f"/api/workflows/hello-flow/runs?version={hello_flow.version}")
    assert workflow_runs.status_code == 200
    assert workflow_runs.json()["workflow"] == "hello-flow"
    assert workflow_runs.json()["current_version"] == hello_flow.version
    assert [run["run_id"] for run in workflow_runs.json()["runs"]] == ["hello-run"]


def test_resume_endpoint_reuses_checkpointed_nodes():
    state = {"fetch_calls": 0, "render_calls": 0, "fail": True}

    @node(id="resume_fetch_api")
    async def resume_fetch_api(ctx, seed: str):
        state["fetch_calls"] += 1
        return [seed, f"{seed}!"]

    @node(id="resume_render_api")
    async def resume_render_api(ctx, text: str):
        state["render_calls"] += 1
        if state["fail"]:
            raise RuntimeError("boom")
        return text.upper()

    @workflow(name="resume-api-flow")
    def resume_api_flow():
        items = resume_fetch_api(seed=["go"])
        return resume_render_api(text=items)

    app = create_app(workflows=[resume_api_flow])
    client = TestClient(app)

    first = client.post("/api/workflows/resume-api-flow/runs?run_id=resume-api-run")
    assert first.status_code == 200
    assert first.json()["status"] == "pending"
    wait_for_run_status(client, "resume-api-run", "failed")
    assert state["fetch_calls"] == 1

    state["fail"] = False
    resumed = client.post("/api/runs/resume-api-run/resume")
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "pending"
    wait_for_run_status(client, "resume-api-run", "completed")
    assert state["fetch_calls"] == 1

    run = client.get("/api/runs/resume-api-run")
    assert run.status_code == 200
    assert run.json()["outputs"]["resume_render_api_0"] == ["GO", "GO!"]


def test_run_errors_include_node_level_binding_failures():
    @node(id="upstream")
    async def upstream(ctx, value: str):
        return value

    @node(id="downstream")
    async def downstream(ctx, request: str):
        return request

    @workflow(name="binding-failure-flow")
    def binding_failure_flow():
        value = upstream(value=["demo"])
        return downstream(value)

    app = create_app(workflows=[binding_failure_flow])
    client = TestClient(app)

    response = client.post("/api/workflows/binding-failure-flow/runs?run_id=binding-failure-run")
    assert response.status_code == 200
    wait_for_run_status(client, "binding-failure-run", "failed")

    run = client.get("/api/runs/binding-failure-run")
    assert run.status_code == 200
    assert run.json()["nodes"]["downstream_0"]["errors"] == ["'request'"]

    errors = client.get("/api/runs/binding-failure-run/errors")
    assert errors.status_code == 200
    assert len(errors.json()) == 1
    assert errors.json()[0]["node_id"] == "downstream"
    assert errors.json()[0]["message"] == "'request'"


def test_subgraph_runs_become_visible_as_real_workflows():
    @node(id="child_upper")
    async def child_upper(ctx, value: str):
        return value.upper()

    @workflow(name="child-app-flow")
    def child_app_flow(items: list[str]):
        return child_upper(value=items)

    @node(id="parent_seed_subgraph")
    async def parent_seed_subgraph(ctx, value: str):
        return value

    @workflow(name="parent-app-flow")
    def parent_app_flow():
        items = parent_seed_subgraph(value=["one", "two"])
        return run_subgraph(workflow=child_app_flow, id="child_app_subgraph", kwargs={"items": items})

    app = create_app(workflows=[parent_app_flow])
    client = TestClient(app)

    response = client.post("/api/workflows/parent-app-flow/runs?run_id=parent-app-run")
    assert response.status_code == 200
    wait_for_run_status(client, "parent-app-run", "completed")

    run = client.get("/api/runs/parent-app-run")
    assert run.status_code == 200
    child_run_id = run.json()["nodes"]["child_app_subgraph_0"]["child_run_id"]
    assert child_run_id is not None

    workflows = client.get("/api/workflows")
    assert workflows.status_code == 200
    assert {workflow["name"] for workflow in workflows.json()} >= {"parent-app-flow", "child-app-flow"}

    child_graph = client.get("/api/workflows/child-app-flow/graph")
    assert child_graph.status_code == 200
    assert [node["node_id"] for node in child_graph.json()["nodes"]] == ["child_upper"]

    child_run = client.get(f"/api/runs/{child_run_id}")
    assert child_run.status_code == 200
    assert child_run.json()["parent_run_id"] == "parent-app-run"
    assert child_run.json()["parent_node_id"] == "child_app_subgraph_0"
