from __future__ import annotations

from workgraph import trace_workflow
from workgraph.testing import MockLLM, run_test

from examples.workflows import EXAMPLE_WORKFLOWS, conditional_review, fanout_research, iterative_refinement


async def test_example_workflows_trace():
    names = {workflow.name for workflow in EXAMPLE_WORKFLOWS}

    assert names == {
        "example-hello",
        "example-fanout-research",
        "example-conditional-review",
        "example-iterative-refinement",
        "example-scratchpad-collaboration",
    }

    loop_graph, _ = trace_workflow(iterative_refinement)
    assert loop_graph.nodes[1].loop_iterations == 3


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
