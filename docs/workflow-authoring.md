# Workflow Authoring Guide

This guide is for AI agents and developers adding new workflows to `agentic-workgraph`.

The short version:

- write small async node functions
- keep node inputs scalar
- use the workflow function to describe coordination
- register the workflow in an app
- run it through the API and debugger UI
- add tests before you call it done

## Mental Model

`agentic-workgraph` is code-first. Python is the source of truth.

You do not hand-author a graph file. You write:

1. `@node` functions for work
2. a `@workflow` function that wires them together
3. an app that registers one or more workflows with `create_app(...)`

The runtime traces the workflow into a graph, executes mapped list work, records run state, emits live events, and exposes everything through FastAPI and `/ui`.

Workflows can also compose other workflows with `run_subgraph(...)` when a reusable child flow should remain debuggable as its own run instead of being flattened into ordinary nodes.

## Authoring Rules

Follow these rules unless there is a strong reason not to:

- Nodes should be async functions.
- Nodes should do one thing well.
- If a node uses `ctx`, declare it as the first parameter, or immediately after `self`/`cls` on methods.
- Node parameters should be scalar values or structured objects, not pre-batched lists.
- Let the runtime map over lists. Do not write your own per-item loop unless the loop is the point of the workflow.
- Use Pydantic models for contract-shaped inputs and outputs.
- Put external side effects at explicit node boundaries.
- Keep irreversible actions in their own terminal node.
- Prefer fixing bad fixture JSON over adding compatibility code for every old shape.

## Basic Shape

```python
from pydantic import BaseModel

from workgraph import node, workflow


class Greeting(BaseModel):
    text: str


@node(id="compose_greeting", output_schema=Greeting)
async def compose_greeting(ctx, name: str):
    return Greeting(text=f"hello {name}")


@workflow(name="hello-flow")
def hello_flow():
    return compose_greeting(name=["world"])
```

Important details:

- `@node(id=...)` controls the graph node ID shown in the debugger.
- `output_schema=...` makes the runtime validate outputs and gives you cleaner contracts.
- The workflow returns the terminal node call.
- `name=["world"]` is a list, so the runtime creates one item execution for each element.

## What Belongs In A Node

Good node responsibilities:

- fetch data from an API
- normalize a document or record
- call `ctx.llm(...)` once for a specific purpose
- score or critique one artifact
- write one output artifact to disk

Bad node responsibilities:

- “do the whole workflow”
- hidden fan-out plus aggregation plus publishing in one function
- quietly mutating global state
- swallowing failures that the workflow should see

If a step needs progress reporting, use `ctx.progress(...)`.

```python
@node(id="count_step")
async def count_step(ctx, value: int):
    async with ctx.progress(desc="counting") as progress:
        for index in range(10):
            await progress.update((index + 1) / 10, desc=f"step {index + 1}/10")
    return value + 1
```

## Lists, Fan-Out, And Aggregation

The runtime maps scalar nodes over list inputs automatically.

Typical pattern:

1. one node returns `list[T]`
2. a downstream node runs once per item
3. a later node aggregates the resulting list

Use this for:

- research gathering
- per-platform dispatch prep
- batch artifact scoring
- multi-draft critique

Do not manually loop over a list inside a node when you want visibility in the UI. Let the runtime create item records.

## Branching And Loops

Use normal Python control flow in the workflow function.

```python
@workflow(name="review-flow", trace_branches="all")
def review_flow():
    draft = draft_copy(seed=["topic"])
    verdict = check_quality(draft=draft)
    if verdict:
        return publish_ready(draft=draft)
    return revise_draft(draft=draft)
```

For iterative refinement, use a bounded loop. Repeated self-dependent traced calls are modeled as a loop node in the graph UI.

Rules:

- keep loops bounded
- make each iteration explicit
- avoid unbounded “retry until it works” prompt logic

## Subgraph Composition

Use `run_subgraph(...)` when a workflow should call another workflow as a reusable unit.

```python
from workgraph import run_subgraph


@workflow(name="parent-flow")
def parent_flow():
    prepared = prepare_inputs(topic=["subgraphs"])
    return run_subgraph(
        workflow=child_flow,
        id="child_flow_run",
        kwargs={"claims": prepared},
    )
```

Current subgraph semantics:

- the parent graph shows one subgraph node
- the child workflow executes as a real linked run
- the parent node output becomes the child run's `final_output`
- the UI can navigate from the parent node into the child run

Authoring rules for subgraphs:

- Keep data mapping in ordinary Python nodes before or after `run_subgraph(...)`.
- Pass the whole prepared list payload into the child workflow; do not expect per-item child runs.
- Use subgraphs for reusable coordination, not for hiding simple linear steps that should stay visible in one graph.
- Give the subgraph node an explicit `id=` so the parent graph stays readable.

## LLM Usage

Use `ctx.llm(...)` inside a node when the model call is part of the workflow step.

```python
@node(id="critique_copy")
async def critique_copy(ctx, draft: str):
    review = await ctx.llm(
        prompt=f"Critique this draft and return concise notes:\n\n{draft}"
    )
    return {"draft": draft, "review": review}
```

Current project guidance:

- prefer explicit purpose-built prompts per node
- validate structured outputs with Pydantic when possible
- keep one model call per node unless the node is specifically about multi-call orchestration
- use app-level LLM injection when wiring a real deployment

If you need Ollama:

```python
from workgraph import create_app, create_ollama_cloud_llm

app = create_app(
    workflows=[my_workflow],
    llm_callable=create_ollama_cloud_llm(model="kimi-k2.5:cloud"),
)
```

## Filesystem And Network Effects

External effects are supported, but keep them clean.

Preferred pattern:

1. fetch external state in one node
2. transform and critique in later nodes
3. write artifacts in a dedicated output node

This makes failures, retries, resume behavior, and debugger output much easier to reason about.

If you are working with messy historical data:

- fix the JSON fixtures first
- normalize `notes` to at least an array shape
- do not keep widening code paths to support every legacy shape forever

## Registration

Defining a workflow is not enough. It must be registered in an app.

Simple example:

```python
from workgraph import create_app

from .workflows import my_workflow

app = create_app(workflows=[my_workflow])
```

In a larger project, keep a registry module and a shared app entrypoint.

## Testing

Minimum bar for a new workflow:

- one test that traces the workflow graph
- one test that executes a normal run
- one test for the most likely failure or shape edge case

Useful helpers already exist in `workgraph.testing`:

- `MockLLM`
- `run_test(...)`
- `run_test_node(...)`
- `record_trace(...)`
- `replay_trace(...)`
- graph snapshot assertions

If the workflow uses fixtures, keep them small and normalized. If the fixtures are broken, repair them instead of burying the problem in code.

## UI Verification

Before you call a workflow done, verify it in `/ui`.

Checklist:

- the workflow appears under `/api/workflows`
- the graph shape looks correct
- node IDs are readable
- subgraph nodes, if any, show the title-bar indicator and open the child run
- progress updates show up while the run is live
- final artifact highlights the useful output
- trace and item records are understandable

If the workflow has streaming or long-running steps, launch it from the UI at least once and confirm websocket updates are visible before completion.

## Where To Put Things

In this repo:

- reusable workflow examples: [`examples/workflows.py`](../examples/workflows.py)
- example app: [`examples/app.py`](../examples/app.py)
- demo app: [`demo_app.py`](../demo_app.py)

In downstream projects:

- keep workflow code near the project’s real data and prompts
- keep one shared app entrypoint
- keep fixtures curated and intentionally shaped

## Practical Build Order

When adding a real workflow, build it in this order:

1. shape the input fixtures
2. implement the smallest useful node chain
3. add output schemas
4. run it end-to-end without the UI
5. verify it in `/ui`
6. add one real LLM step
7. add tests
8. only then add publishing or irreversible actions

That order keeps failures cheap and makes the debugger useful from the first slice.

## Common Mistakes

- passing huge unstructured dicts between every node
- hiding branching logic inside prompts
- mixing preparation and publishing in one node
- adding compatibility code for every malformed old document
- skipping the UI check and only trusting unit tests
- using one giant node because it feels faster

If a workflow feels impossible to observe, it is probably too coarse.
