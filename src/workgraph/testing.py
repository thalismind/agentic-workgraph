from __future__ import annotations

import json
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .context import Context, Scratchpad
from .core import Executor, trace_workflow, workflow
from .models import NodeError, StreamEnvelope
from .store import InMemoryStore


@dataclass
class MockCall:
    node_id: str
    prompt: str
    params: dict[str, Any]
    response: Any = None
    error: str | None = None


class _NodeMock:
    def __init__(self, mock_llm: "MockLLM", node_id: str) -> None:
        self.mock_llm = mock_llm
        self.node_id = node_id

    def respond(self, value: Any) -> None:
        self.mock_llm.behaviors[self.node_id] = deque([("value", value)])

    def respond_sequence(self, values: list[Any]) -> None:
        self.mock_llm.behaviors[self.node_id] = deque(("value", value) for value in values)

    def respond_with(self, callback: Callable[..., Any]) -> None:
        self.mock_llm.behaviors[self.node_id] = deque([("callback", callback)])

    def raise_error(self, exc: Exception) -> None:
        self.mock_llm.behaviors[self.node_id] = deque([("error", exc)])

    def stream(self, tokens: list[str], response: Any) -> None:
        self.mock_llm.behaviors[self.node_id] = deque([("stream", StreamEnvelope(tokens=tokens, response=response))])

    def stream_sequence(self, values: list[tuple[list[str], Any]]) -> None:
        self.mock_llm.behaviors[self.node_id] = deque(
            ("stream", StreamEnvelope(tokens=tokens, response=response)) for tokens, response in values
        )


class MockLLM:
    def __init__(self) -> None:
        self.behaviors: dict[str, deque[tuple[str, Any]]] = defaultdict(deque)
        self.calls: dict[str, list[MockCall]] = defaultdict(list)
        self.call_order: list[MockCall] = []

    def on(self, node_id: str) -> _NodeMock:
        return _NodeMock(self, node_id)

    def on_any(self) -> _NodeMock:
        return _NodeMock(self, "*")

    async def __call__(self, *, prompt: str, node_id: str, **kwargs: Any) -> Any:
        call = MockCall(node_id=node_id, prompt=prompt, params=kwargs)
        self.calls[node_id].append(call)
        self.call_order.append(call)
        behavior_queue = self.behaviors[node_id] or self.behaviors["*"]
        if not behavior_queue:
            raise RuntimeError(f"No mock response configured for node '{node_id}'")
        behavior_type, payload = behavior_queue[0]
        if len(behavior_queue) > 1:
            behavior_queue.popleft()
        if behavior_type == "error":
            call.error = str(payload)
            raise payload
        if behavior_type == "callback":
            response = payload(prompt=prompt, node_id=node_id, **kwargs)
        elif behavior_type == "stream":
            response = payload
        else:
            response = payload
        call.response = response
        return response

    def call_count(self, node_id: str) -> int:
        return len(self.calls[node_id])

    def last_call(self, node_id: str) -> MockCall:
        return self.calls[node_id][-1]

    def all_calls(self, node_id: str) -> list[MockCall]:
        return list(self.calls[node_id])


class TestRedis(InMemoryStore):
    pass


def _json_safe(value: Any) -> Any:
    if isinstance(value, StreamEnvelope):
        return {
            "kind": "stream",
            "tokens": list(value.tokens),
            "response": _json_safe(value.response),
        }
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    return value


@dataclass
class TraceCallRecord:
    node_id: str
    prompt: str
    params: dict[str, Any]
    response: Any = None
    error: str | None = None


@dataclass
class TraceRecording:
    workflow: str
    inputs: dict[str, Any]
    outputs: dict[str, Any]
    status: str
    calls: list[TraceCallRecord]

    def save(self, path: str) -> None:
        Path(path).write_text(
            json.dumps(
                {
                    "workflow": self.workflow,
                    "inputs": self.inputs,
                    "outputs": self.outputs,
                    "status": self.status,
                    "calls": [
                        {
                            "node_id": call.node_id,
                            "prompt": call.prompt,
                            "params": call.params,
                            "response": call.response,
                            "error": call.error,
                        }
                        for call in self.calls
                    ],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )

    @classmethod
    def load(cls, path: str) -> "TraceRecording":
        payload = json.loads(Path(path).read_text())
        return cls(
            workflow=payload["workflow"],
            inputs=payload["inputs"],
            outputs=payload["outputs"],
            status=payload["status"],
            calls=[TraceCallRecord(**call) for call in payload["calls"]],
        )


@dataclass
class ReplayResult:
    recording: TraceRecording
    run: Any
    differences: list[str]
    mode: str

    def all_passed(self) -> bool:
        return not self.differences

    def report(self) -> str:
        if not self.differences:
            return "REPLAY OK"
        lines = ["REPLAY DIVERGENCE:"]
        lines.extend(f"  - {difference}" for difference in self.differences)
        return "\n".join(lines)


class RecordingLLM:
    def __init__(self, wrapped) -> None:
        self.wrapped = wrapped
        self.calls: list[TraceCallRecord] = []

    async def __call__(self, *, prompt: str, node_id: str, **kwargs: Any) -> Any:
        record = TraceCallRecord(node_id=node_id, prompt=prompt, params=_json_safe(kwargs))
        self.calls.append(record)
        try:
            response = await self.wrapped(prompt=prompt, node_id=node_id, **kwargs)
        except Exception as exc:  # noqa: BLE001
            record.error = str(exc)
            raise
        record.response = _json_safe(response)
        return response


def test_context(
    *,
    llm=None,
    errors: list[NodeError] | None = None,
    scratchpad: Scratchpad | None = None,
    run_id: str = "test-run",
    node_id: str = "test-node_0",
    node_name: str = "test-node",
    item_index: int | None = 0,
) -> Context:
    return Context(
        run_id=run_id,
        node_id=node_id,
        node_name=node_name,
        item_index=item_index,
        llm_callable=llm or MockLLM(),
        scratchpad=scratchpad,
        errors=errors,
    )


async def run_test(workflow, *args: Any, llm: MockLLM | None = None, on_event=None, **kwargs: Any):
    store = InMemoryStore()
    if on_event is not None:
        original_publish = store.publish_event

        def publish_event(run_id: str, event: dict) -> None:
            original_publish(run_id, event)
            on_event(event)

        store.publish_event = publish_event  # type: ignore[method-assign]
    executor = Executor(store=store, llm_callable=llm or MockLLM())
    return await executor.run(workflow, *args, **kwargs)


async def run_test_node(node_callable, *, items: list[Any], llm: MockLLM | None = None):
    node_def = getattr(node_callable, "_node_def")
    item_param = next(iter(node_def.signature.parameters))

    @workflow(name=f"test-{node_def.node_id}")
    def node_workflow():
        return node_callable(**{item_param: items})

    run = await run_test(node_workflow, llm=llm)
    return run.outputs[f"{node_def.node_id}_0"]


async def record_trace(workflow_def, *args: Any, llm, **kwargs: Any) -> TraceRecording:
    recorder = RecordingLLM(llm)
    run = await run_test(workflow_def, *args, llm=recorder, **kwargs)
    return TraceRecording(
        workflow=workflow_def.name,
        inputs={"args": _json_safe(args), "kwargs": _json_safe(kwargs)},
        outputs=_json_safe(run.outputs),
        status=str(run.status),
        calls=recorder.calls,
    )


def _restore_response(payload: Any) -> Any:
    if isinstance(payload, dict) and payload.get("kind") == "stream":
        return StreamEnvelope(tokens=payload["tokens"], response=payload["response"])
    return payload


async def replay_trace(workflow_def, *, trace_path: str, mode: str = "strict"):
    recording = TraceRecording.load(trace_path)
    mock = MockLLM()
    scripted: dict[str, list[Any]] = defaultdict(list)
    for call in recording.calls:
        if call.error is not None:
            scripted[call.node_id].append(RuntimeError(call.error))
        else:
            scripted[call.node_id].append(_restore_response(call.response))

    for node_id, responses in scripted.items():
        if responses and isinstance(responses[0], Exception):
            mock.on(node_id).raise_error(responses[0])
        elif responses and isinstance(responses[0], StreamEnvelope):
            mock.on(node_id).stream_sequence([(value.tokens, value.response) for value in responses])
        else:
            mock.on(node_id).respond_sequence(responses)

    run = await run_test(
        workflow_def,
        *recording.inputs.get("args", []),
        llm=mock,
        **recording.inputs.get("kwargs", {}),
    )
    differences: list[str] = []
    replay_outputs = _json_safe(run.outputs)
    recorded_nodes = [call.node_id for call in recording.calls]
    replay_nodes = [call.node_id for call in mock.call_order]

    if replay_nodes != recorded_nodes:
        differences.append(f"call order diverged: recorded={recorded_nodes} replayed={replay_nodes}")

    for index, recorded_call in enumerate(recording.calls):
        if index >= len(mock.call_order):
            break
        replay_call = mock.call_order[index]
        if recorded_call.prompt != replay_call.prompt:
            differences.append(
                f"prompt diverged at call {index} ({recorded_call.node_id}): "
                f"recorded={recorded_call.prompt!r} replayed={replay_call.prompt!r}"
            )
        if recorded_call.params != _json_safe(replay_call.params):
            differences.append(
                f"params diverged at call {index} ({recorded_call.node_id})"
            )

    if mode == "strict":
        if replay_outputs != recording.outputs:
            differences.append(f"outputs diverged: recorded={recording.outputs!r} replayed={replay_outputs!r}")
        if str(run.status) != recording.status:
            differences.append(f"status diverged: recorded={recording.status} replayed={run.status}")
    elif mode == "inputs_only":
        differences = []

    return ReplayResult(recording=recording, run=run, differences=differences, mode=mode)


def assert_graph_snapshot(workflow, *, snapshot_path: str) -> None:
    graph, _calls = trace_workflow(workflow)
    payload = {
        "workflow": graph.workflow,
        "version": graph.version,
        "nodes": [
            {
                "instance_id": node.instance_id,
                "node_id": node.node_id,
                "depends_on": node.depends_on,
                "output_schema": node.output_schema,
                "retries": node.retries,
                "item_retries": node.item_retries,
                "concurrency": node.concurrency,
            }
            for node in graph.nodes
        ],
        "edges": [[edge.from_node, edge.to_node] for edge in graph.edges],
    }
    path = Path(snapshot_path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return
    expected = json.loads(path.read_text())
    assert payload == expected
