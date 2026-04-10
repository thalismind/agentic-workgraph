from __future__ import annotations

from workgraph import trace_workflow
from workgraph.testing import MockLLM, run_test

from examples.workflows import (
    EXAMPLE_WORKFLOWS,
    conditional_review,
    fanout_research,
    iterative_refinement,
    live_weather_capture,
    serial_progress,
    subgraph_parent,
)


async def test_example_workflows_trace():
    names = {workflow.name for workflow in EXAMPLE_WORKFLOWS}

    assert names == {
        "example-hello",
        "example-serial-progress",
        "example-fanout-research",
        "example-conditional-review",
        "example-iterative-refinement",
        "example-subgraph-child",
        "example-subgraph-parent",
        "example-live-weather-capture",
        "example-scratchpad-collaboration",
    }

    loop_graph, _ = trace_workflow(iterative_refinement)
    assert loop_graph.nodes[1].loop_iterations == 3
    serial_graph, _ = trace_workflow(serial_progress)
    assert [node.node_id for node in serial_graph.nodes] == [
        "count_stage_one",
        "count_stage_two",
        "count_stage_three",
    ]
    subgraph_graph, _ = trace_workflow(subgraph_parent)
    assert [node.node_id for node in subgraph_graph.nodes] == [
        "subgraph_seed_topics",
        "run_child_subgraph",
        "subgraph_publish_report",
    ]
    assert subgraph_graph.nodes[1].node_kind == "subgraph"


async def test_example_fanout_research_runs_with_mock_llm():
    mock = MockLLM()
    mock.on("summarize_topic").stream_sequence(
        [
            (["a "], {"summary": "A", "confidence": 0.9}),
            (["b "], {"summary": "B", "confidence": 0.91}),
            (["c "], {"summary": "C", "confidence": 0.92}),
        ]
    )

    run = await run_test(fanout_research, llm=mock)

    assert run.status == "completed"
    assert len(run.outputs["synthesize_brief_0"]) == 3


async def test_example_conditional_review_traces_both_paths():
    graph, _ = trace_workflow(conditional_review)

    assert [node.node_id for node in graph.nodes] == ["draft_answer", "review_answer", "revise_answer"]
    assert any("merged truthy and falsy graph paths" in warning for warning in graph.warnings)


async def test_example_live_weather_capture_traces_external_effect_flow():
    graph, _ = trace_workflow(live_weather_capture)

    assert [node.node_id for node in graph.nodes] == [
        "fetch_live_weather",
        "capture_weather_site",
        "summarize_weather_capture",
    ]
