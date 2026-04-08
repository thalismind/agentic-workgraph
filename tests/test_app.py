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

    response = client.get("/api/workflows")
    assert response.status_code == 200
    assert response.json()[0]["name"] == "hello-flow"

    graph = client.get("/api/workflows/hello-flow/graph")
    assert graph.status_code == 200
    assert graph.json()["nodes"][0]["node_id"] == "hello"

    versions = client.get("/api/workflows/hello-flow/versions")
    assert versions.status_code == 200
    assert versions.json()["versions"] == [hello_flow.version]

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

    timeline = client.get("/api/runs/stream-run/timeline")
    assert timeline.status_code == 200
    row = next(item for item in timeline.json() if item["node_id"] == "stream_hello_0")
    assert row["duration_ms"] is not None
    assert row["started_at"] is not None
    assert row["finished_at"] is not None
