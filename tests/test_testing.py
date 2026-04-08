from __future__ import annotations

import json

from pydantic import BaseModel

from workgraph import MockLLM, assert_graph_snapshot, node, run_test, workflow


class StructuredAnswer(BaseModel):
    answer: str
    confidence: float


@node(id="ask-model", output_schema=StructuredAnswer, item_retries=1)
async def ask_model(question: str, ctx):
    return await ctx.llm(prompt=f"Answer this: {question}")


@workflow(name="mocked-flow")
def mocked_flow():
    return ask_model(question=["What is workgraph?"])


async def test_mock_llm_retries_with_validation_feedback():
    mock = MockLLM()
    mock.on("ask-model").respond_sequence(
        [
            {"answer": "draft", "confidence": "high"},
            {"answer": "final", "confidence": 0.9},
        ]
    )

    run = await run_test(mocked_flow, llm=mock)

    assert run.status == "completed"
    assert mock.call_count("ask-model") == 2
    assert "rejected by validation" in mock.last_call("ask-model").prompt
    assert run.outputs["ask-model_0"][0].answer == "final"


def test_graph_snapshot_round_trip(tmp_path):
    snapshot_path = tmp_path / "snapshots" / "mocked-flow.graph.json"

    assert_graph_snapshot(mocked_flow, snapshot_path=str(snapshot_path))
    payload = json.loads(snapshot_path.read_text())

    assert payload["workflow"] == "mocked-flow"
    assert payload["nodes"][0]["node_id"] == "ask-model"
    assert_graph_snapshot(mocked_flow, snapshot_path=str(snapshot_path))
