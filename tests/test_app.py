from __future__ import annotations

from fastapi.testclient import TestClient

from workgraph import create_app, node, workflow


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
