from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from workgraph import create_app, node, workflow
from workgraph.testing import MockLLM
from workgraph.store import InMemoryStore


@node(id="hello")
async def hello(name: str, ctx):
    return f"hello {name}"


@workflow(name="hello-flow")
def hello_flow():
    return hello(name=["world"])


def test_app_exposes_workflow_graph():
    app = create_app(workflows=[hello_flow])
    client = TestClient(app)

    ui = client.get("/ui")
    assert ui.status_code == 200
    assert "Workflow History" in ui.text
    assert "graph-warnings" in ui.text
    assert "run-workflow-button" in ui.text

    ui_script = client.get("/ui/static/app.js")
    assert ui_script.status_code == 200
    assert './graph.js' in ui_script.text
    assert './router.js' in ui_script.text
    assert './state.js' in ui_script.text
    assert "loadNodeInspector" in ui_script.text
    assert "scheduleTraceRefresh" in ui_script.text
    assert "applyEvent" in ui_script.text
    assert "launchWorkflowRun" in ui_script.text
    assert '"hashchange"' in ui_script.text

    graph_script = client.get("/ui/static/graph.js")
    assert graph_script.status_code == 200
    assert "computeGraphLayout" in graph_script.text
    assert "renderGraph" in graph_script.text
    assert "streamingNodes" in graph_script.text

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

    run = client.post("/api/workflows/hello-flow/runs")
    assert run.status_code == 200
    run_id = run.json()["run_id"]

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


@node(id="slow_hello")
async def slow_hello(name: str, ctx):
    await asyncio.sleep(0.01)
    return f"slow hello {name}"


@workflow(name="slow-flow")
def slow_flow():
    return slow_hello(name=["world"])


@node(id="stream_hello")
async def stream_hello(name: str, ctx):
    return await ctx.llm(prompt=f"hello {name}")


@workflow(name="stream-flow")
def stream_flow():
    return stream_hello(name=["world"])


def test_run_emits_event_history():
    store = InMemoryStore()
    app = create_app(workflows=[slow_flow], store=store)
    client = TestClient(app)
    run_id = "run-ws-test"
    response = client.post(f"/api/workflows/slow-flow/runs?run_id={run_id}")
    assert response.status_code == 200
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

    events = store.event_history["stream-run"]
    stream_events = [event for event in events if event["event"] == "node_stream"]
    assert [event["token"] for event in stream_events] == ["slow ", "hello ", "world"]
    assert any(event["event"] == "node_stream_end" for event in events)

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


def test_run_history_filters_by_workflow_and_version():
    app = create_app(workflows=[hello_flow, stream_flow])
    client = TestClient(app)

    run_a = client.post("/api/workflows/hello-flow/runs?run_id=hello-run")
    run_b = client.post("/api/workflows/stream-flow/runs?run_id=stream-run-2")

    assert run_a.status_code == 200
    assert run_b.status_code == 200

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
    async def resume_fetch_api(seed: str, ctx):
        state["fetch_calls"] += 1
        return [seed, f"{seed}!"]

    @node(id="resume_render_api")
    async def resume_render_api(text: str, ctx):
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
    assert first.json()["status"] == "failed"
    assert state["fetch_calls"] == 1

    state["fail"] = False
    resumed = client.post("/api/runs/resume-api-run/resume")
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "completed"
    assert state["fetch_calls"] == 1

    run = client.get("/api/runs/resume-api-run")
    assert run.status_code == 200
    assert run.json()["outputs"]["resume_render_api_0"] == ["GO", "GO!"]
