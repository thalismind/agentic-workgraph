from __future__ import annotations

from pydantic import BaseModel

from workgraph import Executor, node, trace_workflow, workflow


class Summary(BaseModel):
    text: str
    size: int


@node(id="fetch")
async def fetch(seed: str, ctx):
    return [f"{seed}-a", f"{seed}-bb"]


@node(id="summarize", output_schema=Summary, concurrency=2)
async def summarize(text: str, ctx):
    return {"text": text.upper(), "size": len(text)}


@node(id="synthesize")
async def synthesize(summary: Summary, ctx):
    return f"{summary.text}:{summary.size}"


@workflow(name="demo")
def demo():
    items = fetch(seed=["root"])
    summaries = summarize(text=items)
    return synthesize(summary=summaries)


async def test_trace_workflow_builds_linear_graph():
    graph, calls = trace_workflow(demo)

    assert graph.workflow == "demo"
    assert [node.node_id for node in graph.nodes] == ["fetch", "summarize", "synthesize"]
    assert [edge.model_dump(by_alias=True) for edge in graph.edges] == [
        {"from": "fetch_0", "to": "summarize_0"},
        {"from": "summarize_0", "to": "synthesize_0"},
    ]
    assert [call.instance_id for call in calls] == ["fetch_0", "summarize_0", "synthesize_0"]


async def test_executor_maps_lists_between_nodes():
    executor = Executor()
    run = await executor.run(demo)

    assert run.status == "completed"
    assert run.outputs["fetch_0"] == ["root-a", "root-bb"]
    assert [item.text for item in run.outputs["summarize_0"]] == ["ROOT-A", "ROOT-BB"]
    assert run.outputs["synthesize_0"] == ["ROOT-A:6", "ROOT-BB:7"]
