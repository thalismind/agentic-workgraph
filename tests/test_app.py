from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from workgraph import create_app, node, workflow
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
    assert items.json() == [
        {
            "attempts": 1,
            "errors": [],
            "index": 0,
            "input": "world",
            "output": "hello world",
            "progress": 0.0,
            "status": "completed",
        }
    ]

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
