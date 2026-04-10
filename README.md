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

The full design target lives in [`spec.md`](spec.md).

## Install

Requires Python 3.10+.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
```

Runtime dependencies are documented in [`pyproject.toml`](pyproject.toml), including:

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

The demo app in [`demo_app.py`](demo_app.py) includes:

- `hello-flow`: the smallest end-to-end workflow
- `research-demo`: fan-out summaries, progress updates, stream playback, and traceable runs
- `example-iterative-refinement`: loop modeling in the embedded UI

The example library in [`examples/README.md`](examples/README.md) adds a broader set of runnable workflows for common agentic patterns.

The embedded UI also supports launching a fresh run directly from the selected workflow with the `Run Workflow` button.

## CLI

`agentic-workgraph` also exposes a `workgraph` CLI that talks to an already-running API server. It does not launch the server itself.

List workflows:

```bash
workgraph workflows
```

Inspect a workflow's expected input arguments and defaults:

```bash
workgraph launch-spec thalis-concept-intake-to-packet
```

Launch a workflow with named args:

```bash
workgraph run thalis-concept-intake-to-packet --prompt-text="A cathedral grown from black coral and sea-glass"
```

Watch a run until completion:

```bash
workgraph run thalis-concept-intake-to-packet --wait --prompt-text="A cathedral grown from black coral and sea-glass"
workgraph status <run-id> --watch
```

Print the final artifact after waiting, or fetch it later from a past run:

```bash
workgraph run thalis-concept-intake-to-packet --wait --artifact --prompt-text="A cathedral grown from black coral and sea-glass"
workgraph status <run-id> --watch --artifact
workgraph artifact <run-id>
```

By default the CLI targets `http://127.0.0.1:8081`. Override that with `--base-url` or `WORKGRAPH_BASE_URL`.

## Authoring Model

Minimal example:

```python
from workgraph import node, workflow


@node(id="hello")
async def hello(ctx, name: str):
    return f"hello {name}"


@workflow(name="hello-flow")
def hello_flow():
    return hello(name=["world"])
```

Node functions are scalar. The runtime handles list-shaped execution, concurrency, progress accounting, retries, validation, tracing, and storage.

## Project Layout

- [`src/workgraph`](src/workgraph): runtime, API, storage, tracing, testing helpers
- [`src/workgraph/ui`](src/workgraph/ui): embedded static debugger UI
- [`examples`](examples): runnable example workflows and example app
- [`docs`](docs): agentic pattern documentation and example library notes
- [`tests`](tests): smoke and API coverage
- [`demo_app.py`](demo_app.py): runnable demo workflows
- [`spec.md`](spec.md): design target for v1

## Documentation

- [`docs/workflow-authoring.md`](docs/workflow-authoring.md): how to design, register, test, and verify new workflows
- [`docs/downstream-integration.md`](docs/downstream-integration.md): how to embed project-local workflow packages with shared app wiring, fixtures, prompts, and deployment
- [`docs/example-library.md`](docs/example-library.md): what each example workflow demonstrates
- [`docs/agentic-patterns.md`](docs/agentic-patterns.md): guidance on pipeline, fan-out, branching, loops, scratchpads, and recovery

## Example Library

The example library currently includes:

- `example-hello`
- `example-fanout-research`
- `example-conditional-review`
- `example-iterative-refinement`
- `example-scratchpad-collaboration`
- `example-subgraph-child`
- `example-subgraph-parent`
- `example-live-weather-capture`

Run the example app with:

```bash
.venv/bin/python -m uvicorn examples.app:app --host 0.0.0.0 --port 8081
```

## Ollama

`agentic-workgraph` includes first-party Ollama adapters:

```python
from workgraph import Executor, create_ollama_cloud_llm, create_ollama_llm

local_llm = create_ollama_llm(model="gemma3")
cloud_llm = create_ollama_cloud_llm(model="kimi-k2.5:cloud")

executor = Executor(llm_callable=local_llm)
```

Local defaults:
- base URL: `http://localhost:11434/api`
- no auth required

Direct Ollama Cloud defaults:
- base URL: `https://ollama.com/api`
- requires `OLLAMA_API_KEY`, `OLLAMA_CLOUD_API_KEY`, or an explicit `api_key=...`

The adapter uses Ollama's `generate` API so it fits the current `ctx.llm(prompt=...)` contract without introducing a separate chat-message abstraction.

`example-live-weather-capture` is the real-world reference workflow in the library. It fetches live weather data over HTTP and writes a real screenshot artifact to disk.

## Launching Jobs

There are three straightforward ways to launch a workflow run today.

### From the UI

Open `/ui`, select a workflow, and click `Run Workflow`. The UI calls the existing workflow run API and then selects the new run automatically.

### From a webhook

If another system needs to trigger jobs, the cleanest boundary is the workflow run API:

```bash
curl -X POST http://127.0.0.1:8081/api/workflows/example-fanout-research/runs
```

If you want custom request handling, add your own FastAPI route beside `create_app()` and call the executor directly:

```python
from fastapi import Request

from workgraph import create_app
from examples.workflows import fanout_research

app = create_app(workflows=[fanout_research])


@app.post("/webhooks/research")
async def launch_research(request: Request):
    payload = await request.json()
    run = await app.state.executor.run(
        fanout_research,
        seed=[payload.get("seed", "agentic")],
    )
    return {"run_id": run.run_id, "status": run.status}
```

### On a schedule with cron or CronJobs

For a single host, cron can call the same workflow run API:

```cron
*/30 * * * * curl -fsS -X POST http://127.0.0.1:8081/api/workflows/example-live-weather-capture/runs >/dev/null
```

In Kubernetes, the equivalent is a `CronJob` that hits the same endpoint:

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: workgraph-weather
spec:
  schedule: "*/30 * * * *"
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: OnFailure
          containers:
            - name: trigger
              image: curlimages/curl:8.8.0
              args:
                - -fsS
                - -X
                - POST
                - http://workgraph:8081/api/workflows/example-live-weather-capture/runs
```

The important design point is that UI launches, webhooks, and scheduled jobs can all use the same workflow execution surface instead of separate orchestration code paths.

## Redis

Redis is a required dependency because v1 needs a real state backend. The project includes both an in-memory store and a Redis-backed store adapter. Use Redis when you want shared state across processes or a closer-to-production runtime shape.

## Status

This is an active v1 build, not a finished product. The current implementation already covers the core execution loop, observability, live UI, and Redis support, but the spec remains the authoritative target for anything not yet implemented.
