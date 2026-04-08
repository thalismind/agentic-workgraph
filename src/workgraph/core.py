from __future__ import annotations

import asyncio
import functools
import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from .context import Context, Scratchpad
from .models import (
    EdgeSpec,
    GraphSpec,
    NodeCall,
    NodeCounters,
    NodeSpec,
    NodeStatus,
    RunNodeState,
    RunRecord,
    RunStatus,
    ValidationFailStrategy,
)
from .store import InMemoryStore


_TRACE_STATE: ContextVar["TraceState | None"] = ContextVar("workgraph_trace_state", default=None)


def _schema_name(schema: Any) -> str | None:
    if schema is None:
        return None
    return getattr(schema, "__name__", schema.__class__.__name__)


@dataclass(slots=True)
class NodeDefinition:
    func: Callable[..., Awaitable[Any]]
    node_id: str
    retries: int = 0
    item_retries: int = 0
    timeout: int | None = None
    output_schema: Any = None
    on_validation_fail: ValidationFailStrategy = ValidationFailStrategy.RETRY
    fallback_value: Any = None
    concurrency: int | None = None
    signature: inspect.Signature = field(init=False)

    def __post_init__(self) -> None:
        self.signature = inspect.signature(self.func)

    def bind(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        bound = self.signature.bind_partial(*args, **kwargs)
        bound.apply_defaults()
        return dict(bound.arguments)


@dataclass
class WorkflowDefinition:
    func: Callable[..., Any]
    name: str
    default_model: str | None = None
    redis_url: str | None = None
    stream_delay_ms: int = 50
    version: str = field(init=False)

    def __post_init__(self) -> None:
        self.version = compute_workflow_version(self)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return self.func(*args, **kwargs)


@dataclass
class NodeProxy:
    instance_id: str
    node_id: str
    depends_on: list[str]
    node_def: NodeDefinition
    call_args: dict[str, Any]

    def __bool__(self) -> bool:
        return True


@dataclass
class TraceState:
    workflow: WorkflowDefinition
    calls: list[NodeCall] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)

    def next_instance_id(self, node_id: str) -> str:
        count = self.counters.get(node_id, 0)
        self.counters[node_id] = count + 1
        return f"{node_id}_{count}"

    def register_call(self, node_def: NodeDefinition, bound_args: dict[str, Any]) -> NodeProxy:
        depends_on = sorted(_collect_dependencies(bound_args))
        instance_id = self.next_instance_id(node_def.node_id)
        self.calls.append(
            NodeCall(
                instance_id=instance_id,
                node_id=node_def.node_id,
                depends_on=depends_on,
                bound_args=bound_args,
                node_def=node_def,
            )
        )
        return NodeProxy(
            instance_id=instance_id,
            node_id=node_def.node_id,
            depends_on=depends_on,
            node_def=node_def,
            call_args=bound_args,
        )


def _collect_dependencies(value: Any) -> set[str]:
    if isinstance(value, NodeProxy):
        return {value.instance_id}
    if isinstance(value, dict):
        deps: set[str] = set()
        for item in value.values():
            deps.update(_collect_dependencies(item))
        return deps
    if isinstance(value, (list, tuple, set)):
        deps = set()
        for item in value:
            deps.update(_collect_dependencies(item))
        return deps
    return set()


def _materialize_arg(value: Any, outputs: dict[str, list[Any]]) -> Any:
    if isinstance(value, NodeProxy):
        return outputs[value.instance_id]
    if isinstance(value, dict):
        return {key: _materialize_arg(item, outputs) for key, item in value.items()}
    if isinstance(value, list):
        return [_materialize_arg(item, outputs) for item in value]
    if isinstance(value, tuple):
        return tuple(_materialize_arg(item, outputs) for item in value)
    return value


def node(
    *,
    id: str | None = None,
    retries: int = 0,
    item_retries: int = 0,
    timeout: int | None = None,
    output_schema: Any = None,
    on_validation_fail: str = "retry",
    fallback_value: Any = None,
    concurrency: int | None = None,
):
    def decorator(func: Callable[..., Awaitable[Any]]):
        node_def = NodeDefinition(
            func=func,
            node_id=id or func.__name__,
            retries=retries,
            item_retries=item_retries,
            timeout=timeout,
            output_schema=output_schema,
            on_validation_fail=ValidationFailStrategy(on_validation_fail),
            fallback_value=fallback_value,
            concurrency=concurrency,
        )

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any):
            trace_state = _TRACE_STATE.get()
            if trace_state is not None:
                return trace_state.register_call(node_def, node_def.bind(*args, **kwargs))
            return func(*args, **kwargs)

        wrapper._node_def = node_def  # type: ignore[attr-defined]
        return wrapper

    return decorator


def workflow(
    *,
    name: str | None = None,
    default_model: str | None = None,
    redis_url: str | None = None,
    stream_delay_ms: int = 50,
):
    def decorator(func: Callable[..., Any]):
        wf = WorkflowDefinition(
            func=func,
            name=name or func.__name__,
            default_model=default_model,
            redis_url=redis_url,
            stream_delay_ms=stream_delay_ms,
        )
        functools.update_wrapper(wf, func)
        wf._workflow_def = wf  # type: ignore[attr-defined]
        return wf

    return decorator


def compute_workflow_version(workflow_def: WorkflowDefinition) -> str:
    payload = {
        "workflow_name": workflow_def.name,
        "workflow_source": inspect.getsource(workflow_def.func),
        "config": {
            "default_model": workflow_def.default_model,
            "redis_url": workflow_def.redis_url,
            "stream_delay_ms": workflow_def.stream_delay_ms,
        },
        "nodes": sorted(_referenced_node_payloads(workflow_def.func), key=lambda item: item["node_id"]),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return digest[:12]


def _referenced_node_payloads(func: Callable[..., Any]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for value in func.__globals__.values():
        node_def = getattr(value, "_node_def", None)
        if node_def is None:
            continue
        nodes.append(
            {
                "node_id": node_def.node_id,
                "source": inspect.getsource(node_def.func),
                "retries": node_def.retries,
                "item_retries": node_def.item_retries,
                "timeout": node_def.timeout,
                "output_schema": _schema_name(node_def.output_schema),
                "on_validation_fail": node_def.on_validation_fail.value,
                "concurrency": node_def.concurrency,
            }
        )
    return nodes


def trace_workflow(workflow_def: WorkflowDefinition, *args: Any, **kwargs: Any) -> tuple[GraphSpec, list[NodeCall]]:
    trace_state = TraceState(workflow=workflow_def)
    token = _TRACE_STATE.set(trace_state)
    try:
        workflow_def(*args, **kwargs)
    finally:
        _TRACE_STATE.reset(token)

    nodes = [
        NodeSpec(
            instance_id=call.instance_id,
            node_id=call.node_id,
            depends_on=call.depends_on,
            concurrency=call.node_def.concurrency,
            output_schema=_schema_name(call.node_def.output_schema),
            retries=call.node_def.retries,
            item_retries=call.node_def.item_retries,
        )
        for call in trace_state.calls
    ]
    edges = [
        EdgeSpec.model_validate({"from": dep, "to": call.instance_id})
        for call in trace_state.calls
        for dep in call.depends_on
    ]
    graph = GraphSpec(
        graph_id=f"{workflow_def.name}:{uuid4().hex[:12]}",
        workflow=workflow_def.name,
        version=workflow_def.version,
        nodes=nodes,
        edges=edges,
    )
    return graph, trace_state.calls


async def _validate_output(node_def: NodeDefinition, result: Any) -> Any:
    schema = node_def.output_schema
    if schema is None:
        return result
    if isinstance(result, schema):
        return result
    return schema.model_validate(result)


async def _run_one_item(
    node_def: NodeDefinition,
    *,
    item: Any,
    extra_args: dict[str, Any],
    run_id: str,
    item_index: int,
    llm_callable,
    scratchpad: Scratchpad,
    error_log: list[dict[str, Any]],
) -> Any:
    attempts = node_def.item_retries + 1
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        ctx = Context(
            run_id=run_id,
            node_id=node_def.node_id,
            item_index=item_index,
            llm_callable=llm_callable,
            scratchpad=scratchpad,
            errors=error_log,
        )
        try:
            call = node_def.func(item, ctx=ctx, **extra_args)
            result = await asyncio.wait_for(call, timeout=node_def.timeout) if node_def.timeout else await call
            return await _validate_output(node_def, result)
        except ValidationError as exc:
            last_error = exc
            error_log.append(
                {
                    "node_id": node_def.node_id,
                    "item_index": item_index,
                    "attempt": attempt,
                    "error_type": "validation",
                    "message": str(exc),
                }
            )
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            error_log.append(
                {
                    "node_id": node_def.node_id,
                    "item_index": item_index,
                    "attempt": attempt,
                    "error_type": "exception",
                    "message": str(exc),
                }
            )
        if attempt == attempts:
            break

    if node_def.on_validation_fail is ValidationFailStrategy.FALLBACK:
        return node_def.fallback_value
    if last_error is None:
        raise RuntimeError(f"Node {node_def.node_id} failed without an error")
    raise last_error


async def _run_node(
    call: NodeCall,
    *,
    materialized_args: dict[str, Any],
    run_id: str,
    llm_callable,
    scratchpad: Scratchpad,
    error_log: list[dict[str, Any]],
) -> list[Any]:
    node_def = call.node_def
    item_param = next(iter(node_def.signature.parameters))
    items = materialized_args.pop(item_param)
    if not isinstance(items, list):
        raise TypeError(f"Node {node_def.node_id} expected list input for '{item_param}'")

    if node_def.concurrency == 1:
        results = []
        for index, item in enumerate(items):
            results.append(
                await _run_one_item(
                    node_def,
                    item=item,
                    extra_args=materialized_args,
                    run_id=run_id,
                    item_index=index,
                    llm_callable=llm_callable,
                    scratchpad=scratchpad,
                    error_log=error_log,
                )
            )
        return _flatten_results(results)

    semaphore = asyncio.Semaphore(node_def.concurrency) if node_def.concurrency else None

    async def run_guarded(index: int, item: Any) -> Any:
        if semaphore is None:
            return await _run_one_item(
                node_def,
                item=item,
                extra_args=materialized_args,
                run_id=run_id,
                item_index=index,
                llm_callable=llm_callable,
                scratchpad=scratchpad,
                error_log=error_log,
            )
        async with semaphore:
            return await _run_one_item(
                node_def,
                item=item,
                extra_args=materialized_args,
                run_id=run_id,
                item_index=index,
                llm_callable=llm_callable,
                scratchpad=scratchpad,
                error_log=error_log,
            )

    results = list(await asyncio.gather(*(run_guarded(index, item) for index, item in enumerate(items))))
    return _flatten_results(results)


def _flatten_results(results: list[Any]) -> list[Any]:
    flattened: list[Any] = []
    for result in results:
        if isinstance(result, list):
            flattened.extend(result)
        else:
            flattened.append(result)
    return flattened


class Executor:
    def __init__(self, *, store: InMemoryStore | None = None, llm_callable=None) -> None:
        self.store = store or InMemoryStore()
        self.llm_callable = llm_callable or _default_llm

    async def run(self, workflow_def: WorkflowDefinition, *args: Any, **kwargs: Any) -> RunRecord:
        graph, calls = trace_workflow(workflow_def, *args, **kwargs)
        run_id = uuid4().hex[:12]
        graph = graph.model_copy(update={"graph_id": f"{workflow_def.name}:{run_id}"})
        record = RunRecord(
            run_id=run_id,
            workflow=workflow_def.name,
            version=workflow_def.version,
            status=RunStatus.RUNNING,
            graph=graph,
            nodes={node.instance_id: RunNodeState() for node in graph.nodes},
        )
        self.store.add_run(record)

        outputs: dict[str, list[Any]] = {}
        scratchpad = Scratchpad()
        error_log: list[dict[str, Any]] = []

        try:
            for call in calls:
                state = record.nodes[call.instance_id]
                state.status = NodeStatus.RUNNING
                materialized = {key: _materialize_arg(value, outputs) for key, value in call.bound_args.items()}
                item_param = next(iter(call.node_def.signature.parameters))
                items = materialized[item_param]
                state.counters = NodeCounters(
                    total=len(items),
                    pending=len(items),
                    running=0,
                    completed=0,
                    failed=0,
                )
                result = await _run_node(
                    call,
                    materialized_args=materialized,
                    run_id=run_id,
                    llm_callable=self.llm_callable,
                    scratchpad=scratchpad,
                    error_log=error_log,
                )
                outputs[call.instance_id] = result
                state.output = result
                state.status = NodeStatus.COMPLETED
                state.counters.completed = state.counters.total
                state.counters.pending = 0
            record.status = RunStatus.COMPLETED
            record.outputs = outputs
        except Exception as exc:  # noqa: BLE001
            record.status = RunStatus.FAILED
            state = record.nodes[call.instance_id]
            state.status = NodeStatus.FAILED
            state.errors.append(str(exc))
        finally:
            record.finished_at = record.finished_at or datetime.now(timezone.utc)

        return record


async def _default_llm(*, prompt: str, node_id: str, **kwargs: Any) -> Any:
    raise RuntimeError(f"No LLM configured for node '{node_id}' and prompt '{prompt[:40]}'")


def merge(a: list[Any], b: list[Any]) -> list[Any]:
    return [*a, *b]


async def race(tasks: list[Awaitable[Any]]) -> Any:
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    return next(iter(done)).result()
