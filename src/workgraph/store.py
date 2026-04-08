from __future__ import annotations

from collections import defaultdict

from .models import RunRecord


class InMemoryStore:
    def __init__(self) -> None:
        self.runs: dict[str, RunRecord] = {}
        self.workflow_runs: dict[str, list[str]] = defaultdict(list)

    def add_run(self, run: RunRecord) -> None:
        self.runs[run.run_id] = run
        self.workflow_runs[run.workflow].append(run.run_id)

    def get_run(self, run_id: str) -> RunRecord:
        return self.runs[run_id]

    def list_runs(self, workflow: str | None = None) -> list[RunRecord]:
        if workflow is None:
            return list(self.runs.values())
        return [self.runs[run_id] for run_id in self.workflow_runs.get(workflow, [])]
