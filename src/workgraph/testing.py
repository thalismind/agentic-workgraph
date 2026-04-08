from __future__ import annotations

import json
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core import Executor, trace_workflow
from .models import StreamEnvelope
from .store import InMemoryStore


@dataclass
class MockCall:
    node_id: str
    prompt: str
    params: dict[str, Any]
    response: Any = None


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

    def on(self, node_id: str) -> _NodeMock:
        return _NodeMock(self, node_id)

    async def __call__(self, *, prompt: str, node_id: str, **kwargs: Any) -> Any:
        call = MockCall(node_id=node_id, prompt=prompt, params=kwargs)
        self.calls[node_id].append(call)
        behavior_queue = self.behaviors[node_id]
        if not behavior_queue:
            raise RuntimeError(f"No mock response configured for node '{node_id}'")
        behavior_type, payload = behavior_queue[0]
        if len(behavior_queue) > 1:
            behavior_queue.popleft()
        if behavior_type == "error":
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


async def run_test(workflow, *args: Any, llm: MockLLM | None = None, **kwargs: Any):
    store = InMemoryStore()
    executor = Executor(store=store, llm_callable=llm or MockLLM())
    return await executor.run(workflow, *args, **kwargs)


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
