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
from opentelemetry.trace import Status, StatusCode

from .context import Context, Scratchpad
from .errors import VersionMismatchError
from .models import (
    EdgeSpec,
    GraphSpec,
    ItemRecord,
    ItemStatus,
    NodeCall,
    NodeCounters,
    NodeError,
    NodeSpec,
    NodeStatus,
    RunNodeState,
    RunRecord,
    RunStatus,
    ValidationFailStrategy,
)
from .store import InMemoryStore
from .tracing import Telemetry


_TRACE_STATE: ContextVar["TraceState | None"] = ContextVar("workgraph_trace_state", default=None)
_DEFAULT_STORE = InMemoryStore()


def _schema_name(schema: Any) -> str | None:
    if schema is None:
        return None
    return getattr(schema, "__name__", schema.__class__.__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _duration_ms(started_at: datetime | None, finished_at: datetime | None) -> int | None:
    if started_at is None or finished_at is None:
        return None
    return int((finished_at - started_at).total_seconds() * 1000)


def _event(event: str, run_id: str, **payload: Any) -> dict[str, Any]:
    return {
        "event": event,
        "run_id": run_id,
        "timestamp": _utc_now(),
        **payload,
    }


def _format_validation_feedback(exc: ValidationError) -> str:
    lines = ["Your previous response was rejected by validation:"]
    for error in exc.errors(include_url=False):
        location = ".".join(str(part) for part in error["loc"])
        lines.append(f"- {location}: {error['msg']}")
    lines.append("")
    lines.append("Please correct your response and return valid JSON matching the schema.")
    return "\n".join(lines)


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
    stream_max_messages: int = 500
    stream_ttl_hours: int = 24
    otel_endpoint: str | None = None
    otel_service_name: str = "workgraph"
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
    stream_max_messages: int = 500,
    stream_ttl_hours: int = 24,
    otel_endpoint: str | None = None,
    otel_service_name: str = "workgraph",
):
    def decorator(func: Callable[..., Any]):
        wf = WorkflowDefinition(
            func=func,
            name=name or func.__name__,
            default_model=default_model,
            redis_url=redis_url,
            stream_delay_ms=stream_delay_ms,
            stream_max_messages=stream_max_messages,
            stream_ttl_hours=stream_ttl_hours,
            otel_endpoint=otel_endpoint,
            otel_service_name=otel_service_name,
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
            "stream_max_messages": workflow_def.stream_max_messages,
            "stream_ttl_hours": workflow_def.stream_ttl_hours,
            "otel_endpoint": workflow_def.otel_endpoint,
            "otel_service_name": workflow_def.otel_service_name,
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
    node_instance_id: str,
    run_id: str,
    item_index: int,
    llm_callable,
    scratchpad: Scratchpad,
    error_log: list[NodeError],
    item_record: ItemRecord,
    counters: NodeCounters,
    emit_event,
    record_stream,
    tracer,
) -> Any:
    attempts = node_def.item_retries + 1
    last_error: Exception | None = None
    validation_feedback: str | None = None
    if item_record.started_at is None:
        item_record.started_at = _now()

    async def report_progress(progress_value: float, desc: str | None) -> None:
        item_record.progress = progress_value
        item_record.progress_desc = desc
        emit_event(
            _event(
                "node_progress",
                run_id,
                node_id=node_instance_id,
                item_index=item_index,
                progress=progress_value,
                desc=desc,
            )
        )

    for attempt in range(1, attempts + 1):
        item_record.status = ItemStatus.RUNNING
        item_record.attempts = attempt
        counters.pending = max(0, counters.pending - 1) if attempt == 1 else counters.pending
        counters.running += 1
        emit_event(
            _event(
                "node_counters",
                run_id,
                node_id=node_instance_id,
                item_index=item_index,
                counters=counters.model_dump(),
            )
        )
        try:
            with tracer.start_as_current_span(
                node_def.node_id,
                attributes={
                    "workgraph.run.id": run_id,
                    "workgraph.node.id": node_def.node_id,
                    "workgraph.node.instance_id": node_instance_id,
                    "workgraph.node.attempt": attempt,
                    "workgraph.item.index": item_index,
                },
            ) as span:
                ctx = Context(
                    run_id=run_id,
                    node_id=node_instance_id,
                    node_name=node_def.node_id,
                    item_index=item_index,
                    llm_callable=llm_callable,
                    scratchpad=scratchpad,
                    errors=error_log,
                    validation_feedback=validation_feedback,
                    emit_event=emit_event,
                    record_stream=record_stream,
                    report_progress=report_progress,
                    tracer=tracer,
                )
                call = node_def.func(item, ctx=ctx, **extra_args)
                result = await asyncio.wait_for(call, timeout=node_def.timeout) if node_def.timeout else await call
                validated = await _validate_output(node_def, result)
                item_record.output = validated
                item_record.status = ItemStatus.COMPLETED
                item_record.progress = max(item_record.progress, 1.0)
                item_record.finished_at = _now()
                item_record.duration_ms = _duration_ms(item_record.started_at, item_record.finished_at)
                counters.completed += 1
                span.set_attribute("workgraph.node.status", "ok")
                span.set_attribute("workgraph.validation.passed", True)
                emit_event(
                    _event(
                        "node_counters",
                        run_id,
                        node_id=node_instance_id,
                        item_index=item_index,
                        counters=counters.model_dump(),
                    )
                )
                return validated
        except ValidationError as exc:
            last_error = exc
            validation_feedback = _format_validation_feedback(exc)
            item_record.errors.append(str(exc))
            span.set_attribute("workgraph.node.status", "error")
            span.set_attribute("workgraph.validation.passed", False)
            span.set_attribute("workgraph.validation.errors", json.dumps(exc.errors(include_url=False)))
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            error_log.append(
                NodeError(
                    run_id=run_id,
                    node_id=node_def.node_id,
                    item_index=item_index,
                    attempt=attempt,
                    retry_level="item",
                    error_type="validation",
                    message=str(exc),
                    detail={"error": exc.errors(include_url=False)},
                    node_input={"item": item},
                )
            )
            emit_event(
                _event(
                    "node_error",
                    run_id,
                    node_id=node_instance_id,
                    item_index=item_index,
                    error_type="validation",
                    message=str(exc),
                )
            )
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            item_record.errors.append(str(exc))
            span.set_attribute("workgraph.node.status", "error")
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            error_log.append(
                NodeError(
                    run_id=run_id,
                    node_id=node_def.node_id,
                    item_index=item_index,
                    attempt=attempt,
                    retry_level="item",
                    error_type="exception",
                    message=str(exc),
                    detail={"exception_type": exc.__class__.__name__},
                    node_input={"item": item},
                )
            )
            emit_event(
                _event(
                    "node_error",
                    run_id,
                    node_id=node_instance_id,
                    item_index=item_index,
                    error_type="exception",
                    message=str(exc),
                )
            )
        finally:
            counters.running = max(0, counters.running - 1)
            emit_event(
                _event(
                    "node_counters",
                    run_id,
                    node_id=node_instance_id,
                    item_index=item_index,
                    counters=counters.model_dump(),
                )
            )
        if attempt == attempts:
            break

    if node_def.on_validation_fail is ValidationFailStrategy.FALLBACK:
        item_record.output = node_def.fallback_value
        item_record.status = ItemStatus.COMPLETED
        item_record.progress = max(item_record.progress, 1.0)
        item_record.finished_at = _now()
        item_record.duration_ms = _duration_ms(item_record.started_at, item_record.finished_at)
        counters.completed += 1
        emit_event(
            _event(
                "node_counters",
                run_id,
                node_id=node_instance_id,
                item_index=item_index,
                counters=counters.model_dump(),
            )
        )
        return node_def.fallback_value
    if last_error is None:
        raise RuntimeError(f"Node {node_def.node_id} failed without an error")
    item_record.status = ItemStatus.FAILED
    item_record.finished_at = _now()
    item_record.duration_ms = _duration_ms(item_record.started_at, item_record.finished_at)
    counters.failed += 1
    emit_event(
        _event(
            "node_counters",
            run_id,
            node_id=node_instance_id,
            item_index=item_index,
            counters=counters.model_dump(),
        )
    )
    raise last_error


async def _run_node(
    call: NodeCall,
    *,
    materialized_args: dict[str, Any],
    run_id: str,
    llm_callable,
    scratchpad: Scratchpad,
    error_log: list[NodeError],
    node_state: RunNodeState,
    emit_event,
    stream_max_messages: int,
    store: InMemoryStore,
    tracer,
) -> list[Any]:
    node_def = call.node_def
    item_param = next(iter(node_def.signature.parameters))
    items = materialized_args.pop(item_param)
    if not isinstance(items, list):
        raise TypeError(f"Node {node_def.node_id} expected list input for '{item_param}'")
    if not node_state.items:
        node_state.items = [ItemRecord(index=index, input=item) for index, item in enumerate(items)]

    def make_stream_recorder(item_index: int):
        def record_stream(token: str, _item_index: int) -> dict[str, Any]:
            return store.append_stream_chunk(
                run_id=run_id,
                node_id=call.instance_id,
                item_index=item_index,
                token=token,
                max_messages=stream_max_messages,
            )

        return record_stream

    if node_def.concurrency == 1:
        results = []
        for index, item in enumerate(items):
            results.append(
                await _run_one_item(
                    node_def,
                    item=item,
                    extra_args=materialized_args,
                    node_instance_id=call.instance_id,
                    run_id=run_id,
                    item_index=index,
                    llm_callable=llm_callable,
                    scratchpad=scratchpad,
                    error_log=error_log,
                    item_record=node_state.items[index],
                    counters=node_state.counters,
                    emit_event=emit_event,
                    record_stream=make_stream_recorder(index),
                    tracer=tracer,
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
                node_instance_id=call.instance_id,
                run_id=run_id,
                item_index=index,
                llm_callable=llm_callable,
                scratchpad=scratchpad,
                error_log=error_log,
                item_record=node_state.items[index],
                counters=node_state.counters,
                emit_event=emit_event,
                record_stream=make_stream_recorder(index),
                tracer=tracer,
            )
        async with semaphore:
            return await _run_one_item(
                node_def,
                item=item,
                extra_args=materialized_args,
                node_instance_id=call.instance_id,
                run_id=run_id,
                item_index=index,
                llm_callable=llm_callable,
                scratchpad=scratchpad,
                error_log=error_log,
                item_record=node_state.items[index],
                counters=node_state.counters,
                emit_event=emit_event,
                record_stream=make_stream_recorder(index),
                tracer=tracer,
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
        self.store = store or _DEFAULT_STORE
        self.llm_callable = llm_callable or _default_llm
        self.telemetry = Telemetry(self.store)

    async def run(
        self,
        workflow_def: WorkflowDefinition,
        *args: Any,
        run_id: str | None = None,
        **kwargs: Any,
    ) -> RunRecord:
        return await self._run_workflow(workflow_def, args=args, kwargs=kwargs, run_id_override=run_id)

    async def resume(self, run_id: str) -> RunRecord:
        record = self.store.get_run(run_id)
        workflow_def = self.store.get_workflow(record.workflow)
        current_version = self.store.get_version(record.workflow)
        if record.version != current_version:
            raise VersionMismatchError(run_version=record.version, current_version=current_version)
        return await self._run_workflow(
            workflow_def,
            args=record.workflow_args,
            kwargs=record.workflow_kwargs,
            existing_run=record,
        )

    async def _run_workflow(
        self,
        workflow_def: WorkflowDefinition,
        *,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        existing_run: RunRecord | None = None,
        run_id_override: str | None = None,
    ) -> RunRecord:
        self.store.register_workflow(workflow_def)
        self.telemetry = Telemetry(self.store, service_name=workflow_def.otel_service_name)
        tracer = self.telemetry.get_tracer()
        graph, calls = trace_workflow(workflow_def, *args, **kwargs)
        if existing_run is None:
            run_id = run_id_override or uuid4().hex[:12]
            graph = graph.model_copy(update={"graph_id": f"{workflow_def.name}:{run_id}"})
            record = RunRecord(
                run_id=run_id,
                workflow=workflow_def.name,
                version=workflow_def.version,
                status=RunStatus.RUNNING,
                graph=graph,
                workflow_args=args,
                workflow_kwargs=kwargs,
                nodes={node.instance_id: RunNodeState() for node in graph.nodes},
            )
            self.store.add_run(record)
        else:
            run_id = existing_run.run_id
            record = existing_run
            record.status = RunStatus.RUNNING
            record.graph = graph.model_copy(update={"graph_id": f"{workflow_def.name}:{run_id}"})
            record.finished_at = None
            for node in graph.nodes:
                record.nodes.setdefault(node.instance_id, RunNodeState())

        outputs: dict[str, list[Any]] = dict(record.outputs)
        scratchpad = Scratchpad()
        error_log: list[NodeError] = list(record.errors)
        emit_event = lambda event: self.store.publish_event(run_id, event)
        with tracer.start_as_current_span(
            workflow_def.name,
            attributes={
                "workgraph.run.id": run_id,
                "workgraph.workflow.name": workflow_def.name,
                "workgraph.workflow.version": workflow_def.version,
            },
        ) as run_span:
            emit_event(_event("run_status", run_id, status=record.status.value, workflow=workflow_def.name))

            try:
                for call in calls:
                    state = record.nodes[call.instance_id]
                    if state.status is NodeStatus.COMPLETED and state.checkpoint is not None:
                        outputs[call.instance_id] = state.checkpoint
                        record.outputs[call.instance_id] = state.checkpoint
                        continue
                    state.status = NodeStatus.RUNNING
                    state.started_at = _now()
                    state.finished_at = None
                    state.duration_ms = None
                    emit_event(_event("node_status", run_id, node_id=call.instance_id, status=state.status.value, attempt=1))
                    state.errors = []
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
                    state.items = [ItemRecord(index=index, input=item) for index, item in enumerate(items)]
                    result = await _run_node(
                        call,
                        materialized_args=materialized,
                        run_id=run_id,
                        llm_callable=self.llm_callable,
                        scratchpad=scratchpad,
                        error_log=error_log,
                        node_state=state,
                        emit_event=emit_event,
                        stream_max_messages=workflow_def.stream_max_messages,
                        store=self.store,
                        tracer=tracer,
                    )
                    outputs[call.instance_id] = result
                    record.outputs[call.instance_id] = result
                    state.output = result
                    state.checkpoint = result
                    state.status = NodeStatus.COMPLETED
                    state.finished_at = _now()
                    state.duration_ms = _duration_ms(state.started_at, state.finished_at)
                    state.counters.completed = state.counters.total
                    state.counters.pending = 0
                    emit_event(_event("node_status", run_id, node_id=call.instance_id, status=state.status.value, attempt=1))
                    emit_event(_event("node_output", run_id, node_id=call.instance_id, output=result))
                record.status = RunStatus.COMPLETED
                record.outputs = outputs
                record.errors = error_log
                run_span.set_attribute("workgraph.run.status", "ok")
                emit_event(_event("run_status", run_id, status=record.status.value, workflow=workflow_def.name))
            except Exception as exc:  # noqa: BLE001
                record.status = RunStatus.FAILED
                state = record.nodes[call.instance_id]
                state.status = NodeStatus.FAILED
                state.finished_at = _now()
                state.duration_ms = _duration_ms(state.started_at, state.finished_at)
                state.errors.append(str(exc))
                record.outputs = outputs
                record.errors = error_log
                run_span.set_attribute("workgraph.run.status", "error")
                run_span.set_status(Status(StatusCode.ERROR, str(exc)))
                emit_event(_event("node_status", run_id, node_id=call.instance_id, status=state.status.value, attempt=1))
                emit_event(_event("run_status", run_id, status=record.status.value, workflow=workflow_def.name))
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


def get_version(workflow_name: str, *, store: InMemoryStore | None = None) -> str:
    active_store = store or _DEFAULT_STORE
    return active_store.get_version(workflow_name)


def list_versions(workflow_name: str, *, store: InMemoryStore | None = None) -> list[str]:
    active_store = store or _DEFAULT_STORE
    return active_store.list_versions(workflow_name)


async def resume(run_id: str, *, executor: Executor | None = None) -> RunRecord:
    active_executor = executor or Executor(store=_DEFAULT_STORE)
    return await active_executor.resume(run_id)
