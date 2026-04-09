from __future__ import annotations

import asyncio

from hypothesis import given, settings

from workgraph import node, trace_workflow
from workgraph.testing import run_test, run_test_node
from workgraph.testing_strategies import concurrency_configs, item_lists, workflow_graphs


@settings(max_examples=20, deadline=None)
@given(graph=workflow_graphs(max_nodes=8))
def test_generated_workflow_graphs_complete(graph):
    run = asyncio.run(run_test(graph.as_workflow()))
    assert run.status in {"completed", "failed"}
    assert run.status == "completed"


@settings(max_examples=20, deadline=None)
@given(graph=workflow_graphs(max_nodes=8))
def test_generated_workflow_graphs_preserve_topological_order(graph):
    traced, _calls = trace_workflow(graph.as_workflow())
    positions = {node.instance_id: index for index, node in enumerate(traced.nodes)}
    for edge in traced.edges:
        assert positions[edge.from_node] < positions[edge.to_node]


@settings(max_examples=20, deadline=None)
@given(items=item_lists(min_size=1, max_size=30), configured=concurrency_configs())
def test_concurrency_bound_is_respected(items, configured):
    concurrency = configured or 4
    active = 0
    max_active = 0

    @node(id="tracked_property", concurrency=concurrency)
    async def tracked_property(ctx, value: int):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0)
        active -= 1
        return value

    outputs = asyncio.run(run_test_node(tracked_property, items=items))

    assert outputs == items
    assert max_active <= concurrency
