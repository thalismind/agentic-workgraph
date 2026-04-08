from __future__ import annotations

from pydantic import BaseModel

from workgraph import Executor, VersionMismatchError, get_version, list_versions, node, trace_workflow, workflow
from workgraph.store import InMemoryStore


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
    assert [item.status for item in run.nodes["summarize_0"].items] == ["completed", "completed"]
    assert [item.output.text for item in run.nodes["summarize_0"].items] == ["ROOT-A", "ROOT-BB"]


async def test_resume_skips_completed_nodes():
    state = {"fetch_calls": 0, "render_calls": 0, "fail": True}

    @node(id="fetch_resume")
    async def fetch_resume(seed: str, ctx):
        state["fetch_calls"] += 1
        return [seed, f"{seed}!"]

    @node(id="render_resume")
    async def render_resume(text: str, ctx):
        state["render_calls"] += 1
        if state["fail"]:
            raise RuntimeError("boom")
        return text.upper()

    @workflow(name="resume-flow")
    def resume_flow():
        items = fetch_resume(seed=["go"])
        return render_resume(text=items)

    store = InMemoryStore()
    executor = Executor(store=store)
    failed_run = await executor.run(resume_flow)

    assert failed_run.status == "failed"
    assert state["fetch_calls"] == 1

    state["fail"] = False
    resumed_run = await executor.resume(failed_run.run_id)

    assert resumed_run.status == "completed"
    assert state["fetch_calls"] == 1
    assert resumed_run.outputs["fetch_resume_0"] == ["go", "go!"]
    assert resumed_run.outputs["render_resume_0"] == ["GO", "GO!"]
    assert [item.status for item in resumed_run.nodes["render_resume_0"].items] == ["completed", "completed"]


async def test_version_listing_and_resume_mismatch():
    store = InMemoryStore()
    executor = Executor(store=store)

    @node(id="stable_node")
    async def stable_node(value: str, ctx):
        return value

    @workflow(name="versioned")
    def versioned_v1():
        return stable_node(value=["v1"])

    run = await executor.run(versioned_v1)
    assert get_version("versioned", store=store) == versioned_v1.version
    assert list_versions("versioned", store=store) == [versioned_v1.version]

    @workflow(name="versioned")
    def versioned_v2():
        return stable_node(value=["v2"])

    store.register_workflow(versioned_v2)

    assert list_versions("versioned", store=store) == [versioned_v1.version, versioned_v2.version]

    try:
        await executor.resume(run.run_id)
    except VersionMismatchError as exc:
        assert exc.run_version == versioned_v1.version
        assert exc.current_version == versioned_v2.version
    else:
        raise AssertionError("Expected VersionMismatchError")
