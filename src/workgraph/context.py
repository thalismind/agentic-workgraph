from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Callable

from .models import NodeError, StreamEnvelope


class Scratchpad:
    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def get(self, key: str) -> Any:
        return self._data.get(key)

    async def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    async def cas(self, key: str, expected: Any, new: Any) -> bool:
        if self._data.get(key) != expected:
            return False
        self._data[key] = new
        return True


@dataclass(slots=True)
class ProgressHandle:
    desc: str | None = None
    value: float = 0.0
    reporter: Callable[[float, str | None], Any] | None = None

    async def update(self, amount: float) -> None:
        self.value += amount
        if self.reporter is not None:
            await self.reporter(self.value, self.desc)


class Context:
    def __init__(
        self,
        *,
        run_id: str,
        node_id: str,
        node_name: str,
        item_index: int | None,
        llm_callable,
        scratchpad: Scratchpad | None = None,
        errors: list[NodeError] | None = None,
        validation_feedback: str | None = None,
        emit_event: Callable[[dict[str, Any]], None] | None = None,
        record_stream: Callable[[str, int], dict[str, Any]] | None = None,
        report_progress: Callable[[float, str | None], Any] | None = None,
        tracer=None,
    ) -> None:
        self.run_id = run_id
        self.node_id = node_id
        self.node_name = node_name
        self.item_index = item_index
        self._llm_callable = llm_callable
        self.scratchpad = scratchpad or Scratchpad()
        self._errors = errors if errors is not None else []
        self._validation_feedback = validation_feedback
        self._emit_event = emit_event or (lambda _event: None)
        self._record_stream = record_stream or (lambda _token, _item_index: {})
        self._report_progress = report_progress
        self._tracer = tracer

    @staticmethod
    def _estimate_tokens(value: Any) -> int:
        if value is None:
            return 0
        if isinstance(value, str):
            return max(1, len(value.split())) if value.strip() else 0
        if isinstance(value, list):
            return sum(Context._estimate_tokens(item) for item in value)
        return Context._estimate_tokens(str(value))

    async def llm(self, *, prompt: str, **kwargs: Any) -> Any:
        full_prompt = prompt
        if self._validation_feedback:
            full_prompt = f"{prompt}\n\n{self._validation_feedback}"
        stream = kwargs.pop("stream", True)
        model = kwargs.get("model", "")
        provider = model.split("/", 1)[0] if model and "/" in model else ""
        response = await self._llm_callable(
            prompt=full_prompt,
            node_id=self.node_name,
            node_instance_id=self.node_id,
            stream=stream,
            **kwargs,
        ) if self._tracer is None else None
        if self._tracer is not None:
            with self._tracer.start_as_current_span(
                "llm.complete",
                attributes={
                    "workgraph.run.id": self.run_id,
                    "workgraph.node.id": self.node_name,
                    "workgraph.node.instance_id": self.node_id,
                    "workgraph.item.index": self.item_index if self.item_index is not None else -1,
                    "workgraph.validation.feedback_applied": bool(self._validation_feedback),
                    "llm.model": model,
                    "llm.provider": provider,
                    "llm.tokens.input": self._estimate_tokens(full_prompt),
                },
            ) as span:
                started_at = perf_counter()
                response = await self._llm_callable(
                    prompt=full_prompt,
                    node_id=self.node_name,
                    node_instance_id=self.node_id,
                    stream=stream,
                    **kwargs,
                )
                latency_ms = int((perf_counter() - started_at) * 1000)
                span.set_attribute("llm.latency_ms", latency_ms)
                if isinstance(response, StreamEnvelope):
                    span.set_attribute("llm.tokens.output", self._estimate_tokens(response.tokens))
                    span.set_attribute("llm.stream", True)
                    span.set_attribute("llm.response.format", response.response.__class__.__name__)
                else:
                    span.set_attribute("llm.tokens.output", self._estimate_tokens(response))
                    span.set_attribute("llm.stream", False)
                    span.set_attribute("llm.response.format", response.__class__.__name__)
                cost_usd = getattr(response, "cost_usd", kwargs.get("cost_usd", 0.0))
                span.set_attribute("llm.cost.usd", float(cost_usd))
        if not stream:
            return response
        if isinstance(response, StreamEnvelope):
            for index, token in enumerate(response.tokens):
                record = self._record_stream(token, self.item_index or 0)
                self._emit_event(
                    {
                        "event": "node_stream",
                        "run_id": self.run_id,
                        "node_id": self.node_id,
                        "item_index": self.item_index,
                        "token": token,
                        "stream_id": f"{self.node_id}:{self.item_index}:{index}",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "ts": record.get("ts"),
                    }
                )
            self._emit_event(
                {
                    "event": "node_stream_end",
                    "run_id": self.run_id,
                    "node_id": self.node_id,
                    "item_index": self.item_index,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
            return response.response
        return response

    @asynccontextmanager
    async def progress(self, desc: str | None = None):
        yield ProgressHandle(desc=desc, reporter=self._report_progress)

    async def get_errors(self, node_id: str | None = None) -> list[NodeError]:
        if node_id is None:
            return list(self._errors)
        return [error for error in self._errors if error.node_id == node_id]

    async def has_errors(self) -> bool:
        return bool(self._errors)
