from __future__ import annotations

from pathlib import Path

import pytest

from workgraph import node, workflow
from workgraph.testing import MockLLM, record_trace, replay_trace, run_test_node, test_context as make_test_context


@node(id="sample_echo")
async def sample_echo(value: str, ctx):
    return await ctx.llm(prompt=f"echo {value}")


@workflow(name="sample-flow")
def sample_flow():
    return sample_echo(value=["alpha"])


@node(id="sample_double")
async def sample_double(value: int, ctx):
    return value * 2


@pytest.mark.asyncio
async def test_test_context_uses_mock_llm():
    mock = MockLLM()
    mock.on("ctx-node").respond("ok")
    ctx = make_test_context(llm=mock, node_name="ctx-node", node_id="ctx-node_0")

    result = await ctx.llm(prompt="hello")

    assert result == "ok"
    assert mock.call_count("ctx-node") == 1


@pytest.mark.asyncio
async def test_run_test_node_executes_single_node():
    outputs = await run_test_node(sample_double, items=[2, 3])
    assert outputs == [4, 6]


@pytest.mark.asyncio
async def test_record_and_replay_trace(tmp_path: Path):
    mock = MockLLM()
    mock.on("sample_echo").stream(["alpha ", "done"], "alpha done")

    recording = await record_trace(sample_flow, llm=mock)
    trace_path = tmp_path / "testing.trace.json"
    recording.save(str(trace_path))

    replay = await replay_trace(sample_flow, trace_path=str(trace_path))

    assert replay.all_passed()
    assert replay.run.outputs["sample_echo_0"] == ["alpha done"]
