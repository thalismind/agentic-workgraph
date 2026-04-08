from __future__ import annotations

import asyncio
from collections import defaultdict

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
