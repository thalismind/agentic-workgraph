from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hypothesis import strategies as st

from .core import node, workflow


@dataclass(frozen=True)
class FailureScenario:
    crash_point: int
    timeout_seconds: int
    fail_validation: bool


@dataclass(frozen=True)
class GeneratedWorkflowGraph:
    parent_indices: tuple[int, ...]

    @property
    def size(self) -> int:
        return len(self.parent_indices)

    def as_workflow(self):
        node_wrappers = []
        for index in range(self.size):

            @node(id=f"generated_{index}")
            async def generated_step(value: str, ctx, step_index=index):
                return f"{value}|n{step_index}"

            node_wrappers.append(generated_step)

        @workflow(name=f"generated-graph-{self.size}-{'-'.join(map(str, self.parent_indices))}")
        def generated_workflow():
            outputs: list[Any] = []
            for index, parent_index in enumerate(self.parent_indices):
                if parent_index < 0:
                    current = node_wrappers[index](value=[f"seed-{index}"])
                else:
                    current = node_wrappers[index](value=outputs[parent_index])
                outputs.append(current)
            return outputs[-1]

        return generated_workflow


def item_lists(*, min_size: int = 1, max_size: int = 100):
    return st.lists(st.integers(min_value=0, max_value=500), min_size=min_size, max_size=max_size)


def concurrency_configs():
    return st.one_of(st.none(), st.integers(min_value=1, max_value=6))


def failure_scenarios():
    return st.builds(
        FailureScenario,
        crash_point=st.integers(min_value=0, max_value=5),
        timeout_seconds=st.integers(min_value=1, max_value=10),
        fail_validation=st.booleans(),
    )


def workflow_graphs(*, max_nodes: int = 12, max_edges: int | None = None):
    del max_edges

    def build_graph(size: int):
        if size == 1:
            return st.just(GeneratedWorkflowGraph(parent_indices=(-1,)))
        parent_strategies = [
            st.just(-1),
            *[st.integers(min_value=0, max_value=index - 1) for index in range(1, size)],
        ]
        return st.tuples(*parent_strategies).map(lambda parents: GeneratedWorkflowGraph(parent_indices=parents))

    return st.integers(min_value=1, max_value=max_nodes).flatmap(build_graph)
