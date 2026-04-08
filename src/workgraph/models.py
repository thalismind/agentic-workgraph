from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ValidationFailStrategy(str, Enum):
    RETRY = "retry"
    FALLBACK = "fallback"
    FAIL = "fail"


class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class NodeCounters(BaseModel):
    total: int = 0
    pending: int = 0
    running: int = 0
    completed: int = 0
    failed: int = 0


class NodeSpec(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    instance_id: str
    node_id: str
    depends_on: list[str] = Field(default_factory=list)
    concurrency: int | None = None
    output_schema: str | None = None
    retries: int = 0
    item_retries: int = 0
    status: NodeStatus = NodeStatus.PENDING
    counters: NodeCounters = Field(default_factory=NodeCounters)


class EdgeSpec(BaseModel):
    from_node: str = Field(alias="from")
    to_node: str = Field(alias="to")

    model_config = ConfigDict(populate_by_name=True)


class GraphSpec(BaseModel):
    graph_id: str
    workflow: str
    version: str
    nodes: list[NodeSpec]
    edges: list[EdgeSpec]


class NodeCall(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    instance_id: str
    node_id: str
    depends_on: list[str]
    bound_args: dict[str, Any]
    node_def: Any


class RunNodeState(BaseModel):
    status: NodeStatus = NodeStatus.PENDING
    counters: NodeCounters = Field(default_factory=NodeCounters)
    output: list[Any] | None = None
    errors: list[str] = Field(default_factory=list)


class RunRecord(BaseModel):
    run_id: str
    workflow: str
    version: str
    status: RunStatus = RunStatus.PENDING
    graph: GraphSpec
    outputs: dict[str, list[Any]] = Field(default_factory=dict)
    nodes: dict[str, RunNodeState] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None


NodeCallable = Callable[..., Awaitable[Any]]
