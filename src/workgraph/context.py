from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from .models import NodeError


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

    async def update(self, amount: float) -> None:
        self.value += amount


class Context:
    def __init__(
        self,
        *,
        run_id: str,
        node_id: str,
        item_index: int | None,
        llm_callable,
        scratchpad: Scratchpad | None = None,
        errors: list[NodeError] | None = None,
        validation_feedback: str | None = None,
    ) -> None:
        self.run_id = run_id
        self.node_id = node_id
        self.item_index = item_index
        self._llm_callable = llm_callable
        self.scratchpad = scratchpad or Scratchpad()
        self._errors = errors if errors is not None else []
        self._validation_feedback = validation_feedback

    async def llm(self, *, prompt: str, **kwargs: Any) -> Any:
        full_prompt = prompt
        if self._validation_feedback:
            full_prompt = f"{prompt}\n\n{self._validation_feedback}"
        return await self._llm_callable(prompt=full_prompt, node_id=self.node_id, **kwargs)

    @asynccontextmanager
    async def progress(self, desc: str | None = None):
        yield ProgressHandle(desc=desc)

    async def get_errors(self, node_id: str | None = None) -> list[NodeError]:
        if node_id is None:
            return list(self._errors)
        return [error for error in self._errors if error.node_id == node_id]

    async def has_errors(self) -> bool:
        return bool(self._errors)
