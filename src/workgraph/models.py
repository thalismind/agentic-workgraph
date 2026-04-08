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


class ItemStatus(str, Enum):
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
    loop_iterations: int | None = None
    loop_member_ids: list[str] = Field(default_factory=list)
    status: NodeStatus = NodeStatus.PENDING
    counters: NodeCounters = Field(default_factory=NodeCounters)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None


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
    warnings: list[str] = Field(default_factory=list)


class RunSummary(BaseModel):
    run_id: str
    workflow: str
    version: str
    status: RunStatus
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None
    error_count: int = 0
    node_count: int = 0
    llm_cost_usd: float = 0.0


class WorkflowSummary(BaseModel):
    name: str
    current_version: str
    version_count: int
    run_count: int
    latest_run: RunSummary | None = None


class WorkflowVersionEntry(BaseModel):
    version: str
    is_current: bool
    run_count: int
    latest_run: RunSummary | None = None


class WorkflowVersionsResponse(BaseModel):
    workflow: str
    current_version: str
    versions: list[WorkflowVersionEntry] = Field(default_factory=list)


class WorkflowRunsResponse(BaseModel):
    workflow: str
    current_version: str
    version: str | None = None
    runs: list[RunSummary] = Field(default_factory=list)


class RunLaunchResponse(BaseModel):
    run_id: str
    status: RunStatus
    workflow: str
    version: str


class TimelineEntry(BaseModel):
    node_id: str
    status: NodeStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None


class NodeCall(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    instance_id: str
    display_id: str
    node_id: str
    depends_on: list[str]
    bound_args: dict[str, Any]
    node_def: Any
    iteration_index: int = 0


class NodeError(BaseModel):
    run_id: str
    node_id: str
    item_index: int | None = None
    attempt: int
    retry_level: str
    error_type: str
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)
    node_input: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    span_id: str | None = None


class ItemRecord(BaseModel):
    index: int
    status: ItemStatus = ItemStatus.PENDING
    input: Any = None
    output: Any = None
    errors: list[str] = Field(default_factory=list)
    attempts: int = 0
    progress: float = 0.0
    progress_desc: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None


class StreamChunk(BaseModel):
    index: int
    token: str
    ts: int


class StreamEnvelope(BaseModel):
    tokens: list[str]
    response: Any


class RunNodeState(BaseModel):
    status: NodeStatus = NodeStatus.PENDING
    counters: NodeCounters = Field(default_factory=NodeCounters)
    output: list[Any] | None = None
    errors: list[str] = Field(default_factory=list)
    checkpoint: list[Any] | None = None
    items: list[ItemRecord] = Field(default_factory=list)
    loop_iteration: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None


class RunRecord(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    run_id: str
    workflow: str
    version: str
    status: RunStatus = RunStatus.PENDING
    graph: GraphSpec
    workflow_args: tuple[Any, ...] = Field(default_factory=tuple)
    workflow_kwargs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, list[Any]] = Field(default_factory=dict)
    final_node_id: str | None = None
    final_output: list[Any] | None = None
    nodes: dict[str, RunNodeState] = Field(default_factory=dict)
    errors: list[NodeError] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime | None = None


NodeCallable = Callable[..., Awaitable[Any]]
