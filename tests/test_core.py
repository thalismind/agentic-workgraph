from __future__ import annotations

from pydantic import BaseModel

from workgraph import (
    Executor,
    VersionMismatchError,
    assert_graph_snapshot,
    get_version,
    list_versions,
    node,
    trace_workflow,
    workflow,
)
from workgraph.store import InMemoryStore


class Summary(BaseModel):
    text: str
    size: int


@node(id="fetch")
async def fetch(ctx, seed: str):
    return [f"{seed}-a", f"{seed}-bb"]


@node(id="summarize", output_schema=Summary, concurrency=2)
async def summarize(ctx, text: str):
    return {"text": text.upper(), "size": len(text)}


@node(id="synthesize")
async def synthesize(ctx, summary: Summary):
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


def test_demo_graph_snapshot():
    assert_graph_snapshot(demo, snapshot_path="tests/snapshots/demo.graph.json")


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
    async def fetch_resume(ctx, seed: str):
        state["fetch_calls"] += 1
        return [seed, f"{seed}!"]

    @node(id="render_resume")
    async def render_resume(ctx, text: str):
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
    async def stable_node(ctx, value: str):
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


@node(id="progress_node")
async def progress_node(ctx, value: str):
    async with ctx.progress(desc="working") as progress:
        await progress.update(0.25)
        await progress.update(0.75)
    return value.upper()


@workflow(name="progress-flow")
def progress_flow():
    return progress_node(value=["a"])


async def test_progress_and_timing_are_recorded():
    executor = Executor()
    run = await executor.run(progress_flow, run_id="progress-run")

    node = run.nodes["progress_node_0"]
    item = node.items[0]

    assert run.status == "completed"
    assert node.duration_ms is not None
    assert node.started_at is not None
    assert node.finished_at is not None
    assert item.progress == 1.0
    assert item.progress_desc == "working"
    assert item.duration_ms is not None
    progress_events = [event for event in executor.store.event_history["progress-run"] if event["event"] == "node_progress"]
    assert [event["progress"] for event in progress_events] == [0.25, 1.0]


@node(id="branch_source")
async def branch_source(ctx, value: str):
    return value


@node(id="branch_true")
async def branch_true(ctx, value: str):
    return f"true:{value}"


@node(id="branch_false")
async def branch_false(ctx, value: str):
    return f"false:{value}"


@workflow(name="branch-truthy", trace_branches="truthy")
def branch_truthy():
    value = branch_source(value=["x"])
    if value:
        return branch_true(value=value)
    return branch_false(value=value)


@workflow(name="branch-falsy", trace_branches="falsy")
def branch_falsy():
    value = branch_source(value=["x"])
    if value:
        return branch_true(value=value)
    return branch_false(value=value)


@workflow(name="branch-all", trace_branches="all")
def branch_all():
    value = branch_source(value=["x"])
    if value:
        return branch_true(value=value)
    return branch_false(value=value)


async def test_trace_branch_modes_record_warnings():
    truthy_graph, _ = trace_workflow(branch_truthy)
    falsy_graph, _ = trace_workflow(branch_falsy)
    all_graph, _ = trace_workflow(branch_all)

    assert [node.node_id for node in truthy_graph.nodes] == ["branch_source", "branch_true"]
    assert [node.node_id for node in falsy_graph.nodes] == ["branch_source", "branch_false"]
    assert [node.node_id for node in all_graph.nodes] == ["branch_source", "branch_true", "branch_false"]
    assert any("Boolean condition on traced node" in warning for warning in truthy_graph.warnings)
    assert any("merged truthy and falsy graph paths" in warning for warning in all_graph.warnings)


@node(id="looped")
async def looped(ctx, value: str):
    return value


@workflow(name="loop-warning", max_loop_iterations=2)
def loop_warning():
    value = ["x"]
    for _ in range(3):
        value = looped(value=value)
    return value


async def test_trace_warns_when_node_repeats_beyond_loop_limit():
    graph, calls = trace_workflow(loop_warning)

    assert [call.instance_id for call in calls] == ["looped_0", "looped_1", "looped_2"]
    assert [node.instance_id for node in graph.nodes] == ["looped_loop_0"]
    assert graph.nodes[0].node_id == "looped"
    assert graph.nodes[0].loop_iterations == 3
    assert graph.nodes[0].loop_member_ids == ["looped_0", "looped_1", "looped_2"]
    assert any("max_loop_iterations=2" in warning for warning in graph.warnings)


async def test_loop_runs_share_one_display_node():
    executor = Executor()
    run = await executor.run(loop_warning, run_id="loop-run")

    assert run.status == "completed"
    assert "looped_loop_0" in run.nodes
    assert run.nodes["looped_loop_0"].loop_iteration == 3
    assert len(run.nodes["looped_loop_0"].items) == 3
    assert run.outputs["looped_2"] == ["x"]
