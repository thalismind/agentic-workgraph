# agentic-workgraph

Code-first agentic workflow graphs for Python, with a read-only debugger UI, structured node outputs, live events, and tracing.

`agentic-workgraph` treats Python as the source of truth. You define workflows with decorated async functions, the runtime derives the graph, executes list-shaped node work, records state, and exposes a FastAPI API plus an embedded `/ui` debugger.

## Current v1 surface

- `@node` and `@workflow` decorators
- eager graph tracing from Python workflow definitions
- async execution with list fan-out and per-node concurrency
- Pydantic output validation
- in-memory store and Redis-backed store support
- run history, version metadata, resume support, and checkpoints
- live WebSocket events for run and node updates
- streamed `ctx.llm(...)` token capture and playback
- OpenTelemetry spans and trace inspection APIs
- embedded `/ui` history and debugger surface

The full design target lives in [`spec.md`](/workspace/data/coding/projects/agentic-workgraph/spec.md).

## Install

Requires Python 3.10+.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
```

Runtime dependencies are documented in [`pyproject.toml`](/workspace/data/coding/projects/agentic-workgraph/pyproject.toml), including:

- `fastapi`
- `pydantic`
- `redis`
- `opentelemetry-api`
- `opentelemetry-sdk`
- `uvicorn`

## Quick Start

Run the test suite:

```bash
.venv/bin/python -m pytest -q
```

Launch the demo app:

```bash
.venv/bin/python -m uvicorn demo_app:app --host 0.0.0.0 --port 8081
```

Then open:

- API docs surface: `http://127.0.0.1:8081/docs`
- Embedded debugger UI: `http://127.0.0.1:8081/ui`

The demo app in [`demo_app.py`](/workspace/data/coding/projects/agentic-workgraph/demo_app.py) includes:

- `hello-flow`: the smallest end-to-end workflow
- `research-demo`: fan-out summaries, progress updates, stream playback, and traceable runs

The example library in [`examples/README.md`](/workspace/data/coding/projects/agentic-workgraph/examples/README.md) adds a broader set of runnable workflows for common agentic patterns.

## Authoring Model

Minimal example:

```python
from workgraph import node, workflow


@node(id="hello")
async def hello(name: str, ctx):
    return f"hello {name}"


@workflow(name="hello-flow")
def hello_flow():
    return hello(name=["world"])
```

Node functions are scalar. The runtime handles list-shaped execution, concurrency, progress accounting, retries, validation, tracing, and storage.

## Project Layout

- [`src/workgraph`](/workspace/data/coding/projects/agentic-workgraph/src/workgraph): runtime, API, storage, tracing, testing helpers
- [`src/workgraph/ui`](/workspace/data/coding/projects/agentic-workgraph/src/workgraph/ui): embedded static debugger UI
- [`examples`](/workspace/data/coding/projects/agentic-workgraph/examples): runnable example workflows and example app
- [`docs`](/workspace/data/coding/projects/agentic-workgraph/docs): agentic pattern documentation and example library notes
- [`tests`](/workspace/data/coding/projects/agentic-workgraph/tests): smoke and API coverage
- [`demo_app.py`](/workspace/data/coding/projects/agentic-workgraph/demo_app.py): runnable demo workflows
- [`spec.md`](/workspace/data/coding/projects/agentic-workgraph/spec.md): design target for v1

## Documentation

- [`docs/example-library.md`](/workspace/data/coding/projects/agentic-workgraph/docs/example-library.md): what each example workflow demonstrates
- [`docs/agentic-patterns.md`](/workspace/data/coding/projects/agentic-workgraph/docs/agentic-patterns.md): guidance on pipeline, fan-out, branching, loops, scratchpads, and recovery

## Redis

Redis is a required dependency because v1 needs a real state backend. The project includes both an in-memory store and a Redis-backed store adapter. Use Redis when you want shared state across processes or a closer-to-production runtime shape.

## Status

This is an active v1 build, not a finished product. The current implementation already covers the core execution loop, observability, live UI, and Redis support, but the spec remains the authoritative target for anything not yet implemented.
