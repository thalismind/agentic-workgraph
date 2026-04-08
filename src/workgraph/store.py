from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from typing import Any

from .models import RunRecord


class InMemoryStore:
    def __init__(self) -> None:
        self.runs: dict[str, RunRecord] = {}
        self.workflow_runs: dict[str, list[str]] = defaultdict(list)
        self.workflow_versions: dict[str, list[str]] = defaultdict(list)
        self.current_versions: dict[str, str] = {}
        self.workflows: dict[str, object] = {}
        self.event_subscribers: dict[str, list[asyncio.Queue]] = defaultdict(list)
        self.event_history: dict[str, list[dict]] = defaultdict(list)
        self.stream_records: dict[tuple[str, str, int], list[dict]] = defaultdict(list)
        self.trace_spans: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def add_run(self, run: RunRecord) -> None:
        self.runs[run.run_id] = run
        self.workflow_runs[run.workflow].append(run.run_id)

    def get_run(self, run_id: str) -> RunRecord:
        return self.runs[run_id]

    def list_runs(self, workflow: str | None = None) -> list[RunRecord]:
        if workflow is None:
            return list(self.runs.values())
        return [self.runs[run_id] for run_id in self.workflow_runs.get(workflow, [])]

    def register_workflow(self, workflow) -> None:
        self.workflows[workflow.name] = workflow
        versions = self.workflow_versions[workflow.name]
        if workflow.version not in versions:
            versions.append(workflow.version)
        self.current_versions[workflow.name] = workflow.version

    def get_workflow(self, name: str):
        return self.workflows[name]

    def get_version(self, workflow_name: str) -> str:
        return self.current_versions[workflow_name]

    def list_versions(self, workflow_name: str) -> list[str]:
        return list(self.workflow_versions.get(workflow_name, []))

    def publish_event(self, run_id: str, event: dict) -> None:
        self.event_history[run_id].append(event)
        for queue in list(self.event_subscribers.get(run_id, [])):
            queue.put_nowait(event)

    def subscribe(self, run_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self.event_subscribers[run_id].append(queue)
        for event in self.event_history.get(run_id, []):
            queue.put_nowait(event)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue) -> None:
        subscribers = self.event_subscribers.get(run_id, [])
        if queue in subscribers:
            subscribers.remove(queue)

    def append_stream_chunk(
        self,
        *,
        run_id: str,
        node_id: str,
        item_index: int,
        token: str,
        max_messages: int,
    ) -> dict:
        key = (run_id, node_id, item_index)
        records = self.stream_records[key]
        entry = {"index": len(records), "token": token, "ts": int(time.time() * 1000)}
        records.append(entry)
        if len(records) > max_messages:
            original_count = len(records)
            kept = records[-max_messages:]
            records[:] = [{"_truncated": True, "original_count": original_count, "kept": max_messages}, *kept]
        return entry

    def get_stream(self, run_id: str, node_id: str, item_index: int) -> list[dict]:
        return list(self.stream_records.get((run_id, node_id, item_index), []))

    def add_span(self, run_id: str, span: dict[str, Any]) -> None:
        self.trace_spans[run_id].append(span)

    def get_spans(self, run_id: str) -> list[dict[str, Any]]:
        return list(self.trace_spans.get(run_id, []))


class RedisStore(InMemoryStore):
    def __init__(self, redis_url: str) -> None:
        try:
            import redis
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise ImportError("Redis support requires the 'redis' package to be installed") from exc
        super().__init__()
        self.redis = redis.Redis.from_url(redis_url, decode_responses=True)
        self.redis_url = redis_url

    def add_run(self, run: RunRecord) -> None:
        super().add_run(run)
        self.redis.set(f"run:{run.run_id}", run.model_dump_json())
        self.redis.sadd(f"workflow:{run.workflow}:runs", run.run_id)

    def get_run(self, run_id: str) -> RunRecord:
        if run_id in self.runs:
            return self.runs[run_id]
        payload = self.redis.get(f"run:{run_id}")
        if payload is None:
            raise KeyError(run_id)
        run = RunRecord.model_validate_json(payload)
        self.runs[run_id] = run
        return run

    def list_runs(self, workflow: str | None = None) -> list[RunRecord]:
        if workflow is None:
            run_ids = sorted(self.redis.keys("run:*"))
            return [self.get_run(run_id.split(":", 1)[1]) for run_id in run_ids]
        run_ids = self.redis.smembers(f"workflow:{workflow}:runs")
        return [self.get_run(run_id) for run_id in sorted(run_ids)]

    def register_workflow(self, workflow) -> None:
        super().register_workflow(workflow)
        self.redis.set(f"workflow:{workflow.name}:current_version", workflow.version)
        self.redis.sadd(f"workflow:{workflow.name}:versions", workflow.version)

    def get_version(self, workflow_name: str) -> str:
        version = self.redis.get(f"workflow:{workflow_name}:current_version")
        if version is None:
            return super().get_version(workflow_name)
        return version

    def list_versions(self, workflow_name: str) -> list[str]:
        versions = self.redis.smembers(f"workflow:{workflow_name}:versions")
        if not versions:
            return super().list_versions(workflow_name)
        return sorted(versions)

    def publish_event(self, run_id: str, event: dict) -> None:
        super().publish_event(run_id, event)
        self.redis.rpush(f"run:{run_id}:events", json.dumps(event))

    def append_stream_chunk(
        self,
        *,
        run_id: str,
        node_id: str,
        item_index: int,
        token: str,
        max_messages: int,
    ) -> dict:
        entry = super().append_stream_chunk(
            run_id=run_id,
            node_id=node_id,
            item_index=item_index,
            token=token,
            max_messages=max_messages,
        )
        key = f"run:{run_id}:node:{node_id}:item:{item_index}:stream"
        self.redis.rpush(key, json.dumps(entry))
        return entry

    def get_stream(self, run_id: str, node_id: str, item_index: int) -> list[dict]:
        key = f"run:{run_id}:node:{node_id}:item:{item_index}:stream"
        records = self.redis.lrange(key, 0, -1)
        if records:
            return [json.loads(record) for record in records]
        return super().get_stream(run_id, node_id, item_index)

    def add_span(self, run_id: str, span: dict[str, Any]) -> None:
        super().add_span(run_id, span)
        self.redis.rpush(f"run:{run_id}:spans", json.dumps(span))

    def get_spans(self, run_id: str) -> list[dict[str, Any]]:
        records = self.redis.lrange(f"run:{run_id}:spans", 0, -1)
        if records:
            return [json.loads(record) for record in records]
        return super().get_spans(run_id)


def create_store(redis_url: str | None = None):
    if redis_url:
        return RedisStore(redis_url)
    return InMemoryStore()
