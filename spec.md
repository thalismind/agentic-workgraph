# agentic-workgraph — Design Spec

> **Status**: Draft v0.3  
> **Package name**: `agentic-workgraph` (PyPI) / `workgraph` (import)  
> **Updated**: 2026-04-08

---

## 1. Vision

A developer-first agentic workflow framework where **Python is the source of truth**. Developers write decorated async Python functions that define a directed graph of agent steps. A web UI renders this graph in real time (litegraph-style), showing execution progress, intermediate outputs, and trace data.

Think n8n's visual ergonomics married to the authoring model of Prefect — but purpose-built for LLM agent orchestration.

### Core Principles

1. **Code-first, visual-second**: The graph is derived from annotated Python. The UI is a read-only observer and debugger, never an editor.
2. **Structured by default**: Every node can declare a Pydantic output schema. Validation failures are first-class events with configurable recovery.
3. **Observable from day one**: OpenTelemetry tracing is baked in. Every node execution, retry, and LLM call emits spans.
4. **Fully autonomous**: No human-in-the-loop gates. Errors are recorded as structured data for agentic review and recovery.
5. **Start simple, scale out**: Single-process asyncio by default, with a clear path to distributed workers.

---

## 2. Architecture Overview

```
┌──────────────────────────────────────────────────────────┐
│                    Developer                             │
│             (writes Python workflow)                     │
└────────────────────┬─────────────────────────────────────┘
                     │  decorated async functions
                     ▼
┌──────────────────────────────────────────────────────────┐
│              Workflow Engine (Python / asyncio)           │
│                                                          │
│  ┌────────────┐ ┌───────────┐ ┌────────────────────────┐│
│  │ Graph      │ │ Executor  │ │ OTel Instrumentation   ││
│  │ Builder    │ │ (async)   │ │ (spans, attributes)    ││
│  └─────┬──────┘ └─────┬─────┘ └──────────┬─────────────┘│
│        │              │                   │              │
│        ▼              ▼                   ▼              │
│  ┌──────────────────────────────────────────────────────┐│
│  │       Redis (state + checkpoints + pubsub +         ││
│  │         shared scratchpad + error log)               ││
│  └──────────────────────────────────────────────────────┘│
│                                                          │
│  ┌──────────────────────────────────────────────────────┐│
│  │  FastAPI  (REST API + WebSocket + static UI)        ││
│  └──────────────────────────────────────────────────────┘│
└──────────────────────────────────────────────────────────┘
                     │  websocket
                     ▼
┌──────────────────────────────────────────────────────────┐
│             Web UI (litegraph-based, read-only)          │
│  - live node status (pending/running/done/fail)          │
│  - intermediate output inspection                        │
│  - error log with structured context for agentic review  │
│  - trace waterfall view                                  │
│  - run history                                           │
└──────────────────────────────────────────────────────────┘
```

---

## 3. Python Authoring Model

### 3.1 Node Decorator & the List-In/List-Out Model

Every node accepts and returns a **list**. Internally, every node is a `map` operation over its input. A "single-item" node is simply one where the input list has length 1. This creates a uniform execution model: the length of the input list is the number of work items, which directly drives the progress counters in the UI.

```python
from workgraph import node, workflow, Context
from pydantic import BaseModel

class SummaryOutput(BaseModel):
    summary: str
    confidence: float
    key_topics: list[str]

@node(
    id="summarize",
    retries=0,                         # node-level retries (re-run entire node)
    item_retries=3,                    # item-level retries (re-run single failed item)
    timeout=30,
    output_schema=SummaryOutput,
    on_validation_fail="retry",        # applies to item-level retries
    concurrency=10,                    # max parallel items within this node
)
async def summarize(text: str, ctx: Context) -> SummaryOutput:
    """Process a single item. The framework calls this once per input list element."""
    result = await ctx.llm(
        prompt=f"Summarize: {text}",
        response_model=SummaryOutput,
    )
    return result
```

The developer writes a function that handles **one item**. The framework is responsible for mapping it over the input list, managing concurrency, and reporting progress. This is the key invariant: node functions are always scalar — the framework handles the vectorization.

**How list propagation works**:

```python
@workflow(name="research-pipeline")
def research_pipeline():
    # fetch_urls returns list[str] with 1 item → ["https://..."]
    # scrape_pages receives that list, returns list[Page] with 47 items
    # summarize receives 47 items, maps over them, returns list[SummaryOutput]
    # synthesize receives 47 items, returns list[Report] with 1 item

    urls      = fetch_urls(query="agentic frameworks")   # [1] → [47]
    pages     = scrape_pages(urls=urls)                   # [47] (pass-through)
    summaries = summarize(pages=pages)                    # [47] → [47]
    return synthesize(summaries=summaries)                # [47] → [1]
```

No explicit `parallel()` call needed — the fan-out is implicit in the list length. The executor sees `summarize` received 47 items and runs up to `concurrency` of them simultaneously.

**Progress tracking**: Because every node is a map, progress is just `completed / total`. The framework wraps execution in a `tqdm`-compatible progress tracker:

```python
@node(id="scrape_pages")
async def scrape_pages(url: str, ctx: Context) -> Page:
    # ctx.progress is a tqdm-compatible wrapper that also emits
    # WebSocket counter updates to the graph UI
    async with ctx.progress(desc="Scraping") as pbar:
        html = await fetch(url)
        pbar.update(0.5)              # sub-item progress
        parsed = parse(html)
        pbar.update(0.5)
    return parsed
```

The `ctx.progress` wrapper does two things: it updates a local tqdm bar (for CLI/log output) and emits `node_counters` WebSocket events to the UI. For nodes that don't use `ctx.progress`, the framework still tracks item-level completion automatically.

**Concurrency control per node**:

| `concurrency` value | Behavior |
|---|---|
| `None` (default) | All items run concurrently (asyncio gather) |
| `1` | Sequential — items processed one at a time |
| `N` | Semaphore — at most N items concurrently |

### 3.2 LLM Interface

The `ctx.llm` callable is **provider-agnostic**. Under the hood it delegates to litellm (or any conforming adapter), so the developer can target OpenAI, Anthropic, local models, etc. via a model string.

```python
# The context carries a default model, but nodes can override:
result = await ctx.llm(
    prompt="...",
    model="anthropic/claude-sonnet-4-20250514",   # override default
    response_model=MySchema,                       # optional structured output
    temperature=0.2,
)
```

**Streaming**: By default, all `ctx.llm` calls stream internally. The framework buffers the stream and emits token chunks to the UI via WebSocket with a short configurable delay (default 50ms debounce). This lets observers see chain-of-thought traces as they happen without impacting node execution speed.

```python
# Streaming is the default — no code changes needed.
# The framework intercepts the litellm stream, buffers it, and:
#   1. Emits chunks to the UI (debounced)
#   2. Returns the final result to the node function as usual

result = await ctx.llm(prompt="Think step by step...")
# The UI showed the CoT live; `result` is the completed response.

# To disable streaming for a specific call (e.g., short structured output):
result = await ctx.llm(prompt="...", stream=False)
```

The streaming is **transparent to the node function** — `ctx.llm` always returns the completed result. The UI receives a parallel stream of token events for display.

**Configuration** lives in the workflow or environment, not scattered across nodes:

```python
@workflow(
    name="research-pipeline",
    default_model="anthropic/claude-sonnet-4-20250514",
    redis_url="redis://localhost:6379/0",
    stream_delay_ms=50,                # UI token debounce (0 = real-time)
)
def research_pipeline():
    ...
```

### 3.3 Workflow Composition

Because every node is list-in/list-out, composition is just function chaining. Fan-out and fan-in happen implicitly based on list lengths:

```python
@workflow(name="research-pipeline")
def research_pipeline():
    # Each node returns a list. The next node maps over it.
    urls      = fetch_urls(query="agentic frameworks")   # → [url, url, ...]
    pages     = scrape_pages(urls=urls)                   # → [page, page, ...]
    summaries = summarize(pages=pages)                    # → [summary, summary, ...]
    return synthesize(summaries=summaries)                # → [report]
```

**Fan-out**: When `scrape_pages` returns 47 pages, `summarize` automatically maps over all 47.

**Fan-in**: `synthesize` receives all 47 summaries as its input list. Its function body can aggregate them into a single report and return a list of length 1.

**Conditional**:

```python
@workflow(name="research-pipeline")
def research_pipeline():
    urls      = fetch_urls(query="agentic frameworks")
    pages     = scrape_pages(urls=urls)
    summaries = summarize(pages=pages)

    if needs_deeper_research(summaries):
        extra_urls = find_more_urls(summaries=summaries)
        extra_pages = scrape_pages(urls=extra_urls)
        extra_summaries = summarize(pages=extra_pages)
        summaries = merge(summaries, extra_summaries)

    return synthesize(summaries=summaries)
```

**Explicit serial processing**: If a node must process items sequentially (e.g., each item depends on the previous result), set `concurrency=1`:

```python
@node(id="refine", concurrency=1)
async def refine(draft: str, ctx: Context) -> str:
    return await ctx.llm(prompt=f"Improve: {draft}")
```

### 3.4 Graph Derivation via Eager Tracing

The graph is built using **eager tracing**, inspired by JAX/`torch.fx`. The framework has two execution modes for the workflow function: **trace mode** (builds the graph) and **run mode** (executes it). The same Python code powers both.

#### 3.4.1 How Tracing Works

When a workflow is registered (or a run begins), the framework calls the workflow function in trace mode. In this mode, `@node`-decorated functions don't execute their body — they return **`NodeProxy`** objects that record the call and its dependencies.

```python
class NodeProxy:
    """Returned by @node calls during tracing. Records the graph edge."""
    node_id: str          # e.g. "summarize"
    instance_id: str      # e.g. "summarize_0" (unique per call)
    depends_on: list[str] # instance_ids of upstream NodeProxy args
    node_def: NodeDef     # reference to the decorated function + config
    call_args: dict       # the arguments passed (as proxies or literals)
```

**Dependency tracking**: When a `@node` function receives a `NodeProxy` as an argument, that creates an edge. The tracer inspects all arguments and records which upstream nodes feed into which downstream nodes.

```python
@workflow(name="research-pipeline")
def research_pipeline():
    urls  = fetch_urls(query="agentic frameworks")
    #       ^^^ returns NodeProxy("fetch_urls_0", depends_on=[])

    pages = scrape_pages(urls=urls)
    #       ^^^ receives NodeProxy as `urls` arg
    #       returns NodeProxy("scrape_pages_0", depends_on=["fetch_urls_0"])

    summaries = parallel([summarize(text=p) for p in pages])
    #           ^^^ see section 3.4.3 for how parallel + iteration works

    return synthesize(summaries=summaries)
    #      ^^^ returns NodeProxy("synthesize_0", depends_on=["parallel_group_0"])
```

At the end of the workflow function, the tracer collects all `NodeProxy` objects and their dependency links into a `GraphSpec`.

#### 3.4.2 Trace Mode vs. Run Mode

| Aspect | Trace mode | Run mode |
|---|---|---|
| `@node` call returns | `NodeProxy` (no execution) | Actual result (async execution) |
| Purpose | Build graph structure | Execute the workflow |
| Side effects | None | LLM calls, I/O, scratchpad writes |
| Control flow | Executed (branches taken are recorded) | Executed (same code path) |
| When it happens | On workflow registration + start of each run | After graph is built |

The workflow function is called **twice** per run:
1. **Trace pass**: Builds the graph. Fast, no I/O.
2. **Execution pass**: The executor walks the traced graph, calling node functions for real in topological order.

#### 3.4.3 Dynamic Topology

Because the workflow function runs real Python during tracing, dynamic topology works naturally:

**Dynamic fan-out (list-length driven)**:

Because every node is a map over its input list, the tracer doesn't need special fan-out handling. During tracing, `summarize(pages=pages)` records a single node that depends on `scrape_pages`. The actual fan-out count is unknown until runtime — it's just the length of the list that `scrape_pages` returns.

The tracer records this as a normal node. The executor handles the rest: when `scrape_pages` returns 47 items, the executor maps `summarize` over all 47 with the configured concurrency, updating counters as items complete.

No special `__iter__` on `NodeProxy` needed — the list semantics are handled by the executor, not the tracer.

**Conditional branches**:

```python
if needs_deeper_research(summaries):
    # This branch is traced when needs_deeper_research returns a truthy proxy
    extra = scrape_more(summaries)
```

During tracing, `needs_deeper_research(summaries)` returns a `NodeProxy`. The proxy's truthiness is **configurable**:

| Strategy | Behavior | Use case |
|---|---|---|
| `trace_branches="truthy"` (default) | Proxy is truthy → traces the `if` body | Most cases — trace the happy path |
| `trace_branches="all"` | Traces both branches by running the function twice | Full graph visibility |
| `trace_branches="falsy"` | Proxy is falsy → traces the `else` body | When else is the main path |

With `"all"`, both branches appear in the graph JSON. Nodes in untaken branches show as "skipped" in the UI during execution.

**Loops**:

Loops are traced for one iteration and represented as a **single loop node** in the graph — similar to parallel groups, individual iterations are not shown as separate nodes. The loop node displays a live iteration counter.

```python
@workflow(name="iterative-refinement", max_loop_iterations=5)
def iterative():
    draft = generate()
    for i in range(3):                   # fixed: still one node in the graph
        draft = refine(draft, iteration=i)
    return draft
```

In the UI, the loop node shows:

```
┌──────────────────────────┐
│  ↻  refine               │
│     iteration: 2 / 3     │
│  ✓ 1  ▸ 1  ◦ 1           │
└──────────────────────────┘
```

Clicking the loop node opens the inspector with per-iteration details (input, output, errors, timing).

#### 3.4.4 Graph JSON

The graph JSON is flat and uniform — every node has the same structure. There is no distinction between "single" nodes and "parallel group" nodes. The `counters` field on every node tracks item-level progress at runtime:

```json
{
  "graph_id": "research-pipeline:run_abc123",
  "nodes": [
    {
      "instance_id": "fetch_urls_0",
      "node_id": "fetch_urls",
      "output_schema": "UrlList",
      "depends_on": [],
      "concurrency": null,
      "counters": {"total": 0, "pending": 0, "running": 0, "completed": 0, "failed": 0},
      "status": "pending"
    },
    {
      "instance_id": "scrape_pages_0",
      "node_id": "scrape_pages",
      "depends_on": ["fetch_urls_0"],
      "concurrency": 10,
      "counters": {"total": 0, "pending": 0, "running": 0, "completed": 0, "failed": 0},
      "status": "pending"
    },
    {
      "instance_id": "summarize_0",
      "node_id": "summarize",
      "output_schema": "SummaryOutput",
      "depends_on": ["scrape_pages_0"],
      "concurrency": 10,
      "counters": {"total": 0, "pending": 0, "running": 0, "completed": 0, "failed": 0},
      "status": "pending"
    },
    {
      "instance_id": "synthesize_0",
      "node_id": "synthesize",
      "depends_on": ["summarize_0"],
      "concurrency": null,
      "counters": {"total": 0, "pending": 0, "running": 0, "completed": 0, "failed": 0},
      "status": "pending"
    }
  ],
  "edges": [
    {"from": "fetch_urls_0", "to": "scrape_pages_0"},
    {"from": "scrape_pages_0", "to": "summarize_0"},
    {"from": "summarize_0", "to": "synthesize_0"}
  ]
}
```

At runtime, the graph structure never changes — only `status` and `counters` fields update. The UI renders counter badges on every node. For nodes processing a single item (`total: 1`), the counters are visually hidden and the node just shows a simple status color.

Counter updates are emitted as lightweight WebSocket events:

```json
{
  "event": "node_counters",
  "run_id": "abc123",
  "node_id": "summarize_0",
  "counters": {"total": 47, "pending": 2, "running": 12, "completed": 31, "failed": 2},
  "timestamp": "2026-04-07T14:30:05Z"
}
```

Nodes can also emit **sub-item progress** (from `ctx.progress`), which updates a progress bar within the node's counter badge:

```json
{
  "event": "node_progress",
  "run_id": "abc123",
  "node_id": "scrape_pages_0",
  "item_index": 14,
  "progress": 0.5,
  "desc": "Scraping",
  "timestamp": "2026-04-07T14:30:03Z"
}
```

Individual item details (per-item status, output, errors) are available **only via the REST API**, requested on-demand when the user clicks a node in the inspector:

```
GET /api/runs/{run_id}/nodes/{node_id}/items
GET /api/runs/{run_id}/nodes/{node_id}/items/{index}
```

#### 3.4.5 Limitations & Guardrails

| Limitation | Mitigation |
|---|---|
| Conditional branches: only one branch traced by default | `trace_branches="all"` option; untaken branches marked "skipped" |
| Unbounded loops: can't trace infinite iterations | `max_loop_iterations` config; traced for 1 iteration, counter updates at runtime |
| External state in control flow (e.g., `if db.check(...)`) | Raise `TraceWarning` — external calls during tracing are flagged |
| Proxy truthiness surprises | Clear docs + `TraceWarning` when a proxy is used in a boolean context |

The tracer emits `TraceWarning` diagnostics for anything suspicious (external calls, ambiguous control flow). These appear in the CLI output and the UI's workflow registration panel.

---

## 4. Execution Model

### 4.1 Execution Model: Every Node is a Map

The executor treats every node uniformly: it receives the output list from the upstream node, maps the node function over each item (respecting the `concurrency` setting), collects results into an output list, and passes that to the next node.

```
upstream output: [item_0, item_1, ..., item_N]
                          │
                    ┌─────┼─────┐
                    ▼     ▼     ▼    (up to `concurrency` at a time)
                 node()  node()  node()
                    │     │     │
                    └─────┼─────┘
                          ▼
downstream input: [result_0, result_1, ..., result_N]
```

**Patterns emerge from list manipulation, not special primitives**:

| Pattern | How it works |
|---|---|
| Series | `a = step_a(...); b = step_b(a)` — data dependency, sequential nodes |
| Fan-out | Upstream returns N items → downstream maps over N |
| Fan-in | Node body aggregates input list → returns list of length 1 |
| Concurrency limit | `@node(concurrency=10)` — semaphore per node |
| Sequential map | `@node(concurrency=1)` — items processed one at a time |
| Conditional | `if` / `match` — standard Python control flow |
| Loop | `for` / `while` — re-invoke nodes with updated lists |

The `race()` primitive is the one exception — it still exists for "first-wins, cancel rest" semantics, which can't be expressed as a simple map:

```python
from workgraph import race

# Returns the first successful result, cancels the rest
fastest = race([search_google(query=q), search_bing(query=q)])
```

### 4.2 Retry Model: Item-Level vs. Node-Level

Retries operate at two distinct levels, configured independently:

**Item-level retries** (`item_retries`): When a single item within a node fails (validation error, exception, timeout), only that item is retried — the other items are unaffected. This is the common case for LLM calls where one out of 47 summaries might fail validation.

**Node-level retries** (`retries`): When the node as a whole fails — meaning item-level retries are exhausted and the failure policy escalates, or a systemic error occurs (e.g., rate limit across all items) — the entire node is re-executed from scratch with its full input list.

```
Item fails
  → item_retries remaining? → retry that item (with feedback injection if validation)
  → item_retries exhausted? → on_validation_fail decides:
      "fallback" → use fallback_value for that item, node continues
      "fail"     → item marked failed, node continues other items
                    (node fails overall if ANY items failed with "fail")
  → node failed? → retries remaining? → retry entire node
                 → retries exhausted? → node fails, error recorded
```

**Example configurations**:

```python
# LLM summarization: retry each bad summary 3 times, never restart the whole batch
@node(id="summarize", item_retries=3, retries=0, on_validation_fail="retry")

# Flaky API call: don't retry individual items, but retry the whole node on failure
@node(id="call_api", item_retries=0, retries=3)

# Best-effort: retry each item twice, use fallback for persistent failures
@node(id="enrich", item_retries=2, retries=0, on_validation_fail="fallback",
      fallback_value=EnrichResult(data=None, enriched=False))
```

### 4.3 Structured Output Validation

Each node with `output_schema` validates its return value through Pydantic after execution. Validation failures trigger the **item-level** retry path with automatic feedback injection:

```
[Original prompt]
---
Your previous response was rejected by validation:
- confidence: value is not a valid float (got "high")
- key_topics: field required

Please correct your response and return valid JSON matching the schema.
```

The `on_validation_fail` parameter controls what happens when an item exhausts its `item_retries`:

| Strategy | Behavior |
|---|---|
| `"retry"` (default) | Re-invoke the item with the validation error appended to the prompt. When `item_retries` is exhausted, the item is marked failed. |
| `"fallback"` | Return `fallback_value` for that item and continue. The validation error is recorded in the error log. |
| `"fail"` | Immediately mark the item as failed. If any items fail, the node fails (and may trigger node-level `retries`). |

### 4.3 Error Recording for Agentic Review

All errors — validation failures, exceptions, timeouts, LLM refusals — are recorded as structured error objects in Redis. This is the primary recovery mechanism: downstream agent nodes can query the error log, reason about failures, and decide how to proceed.

**Error record schema**:

```python
class NodeError(BaseModel):
    run_id: str
    node_id: str
    item_index: int | None                # None for node-level errors
    attempt: int                          # which retry attempt (item or node level)
    retry_level: Literal["item", "node"]  # which retry level this error came from
    error_type: Literal[
        "validation", "exception", "timeout", "llm_error"
    ]
    message: str                          # agent-readable summary
    detail: dict                          # structured context
    #   validation → {"field": "confidence", "error": "not a float", ...}
    #   exception  → {"traceback": "...", "exc_type": "ValueError"}
    #   timeout    → {"limit_seconds": 30, "elapsed": 30.01}
    #   llm_error  → {"status": 429, "provider": "anthropic", "body": "..."}
    node_input: dict                      # the input that caused the error
    timestamp: datetime
    span_id: str                          # link to OTel span
```

**Redis storage**:

```
run:{run_id}:errors                    → list (append-only, all errors)
run:{run_id}:node:{node_id}:errors     → list (per-node errors)
```

**Agentic access via Context**: Any downstream node can query the error log and use it for autonomous decision-making:

```python
@node(id="supervisor")
async def supervisor(ctx: Context):
    errors = await ctx.get_errors()                    # all errors in this run
    errors = await ctx.get_errors(node_id="summarize") # from a specific node

    # A supervisor agent reasons about the errors and decides recovery
    plan = await ctx.llm(
        prompt=f"These nodes failed: {errors}. Suggest a recovery plan.",
        response_model=RecoveryPlan,
    )
    return plan
```

**Supervisor pattern**: A common workflow shape is to have a final `supervisor` node that runs only when upstream nodes have errors. This node inspects the error log, re-invokes failed nodes with adjusted parameters, or synthesizes partial results:

```python
@workflow(name="resilient-pipeline")
def resilient_pipeline():
    results = parallel([task_a(), task_b(), task_c()])
    if ctx.has_errors():
        recovery = supervisor()
        results = merge(results, recovery)
    return results
```

---

## 5. State & Data (Redis)

### 5.1 Key Schema

| Key pattern | Type | TTL | Purpose |
|---|---|---|---|
| `run:{run_id}:state` | hash | 7d | Overall run status, start/end time, config |
| `run:{run_id}:graph` | string (JSON) | 7d | Serialized graph structure for UI |
| `run:{run_id}:node:{node_id}` | hash | 7d | Per-node status, output, timing |
| `run:{run_id}:node:{node_id}:checkpoint` | string | 7d | Serialized input + last good output for resume |
| `run:{run_id}:errors` | list | 7d | Append-only error log (NodeError JSON) |
| `run:{run_id}:node:{node_id}:errors` | list | 7d | Per-node error list |
| `run:{run_id}:node:{node_id}:stream` | list (JSON) | 24h | Raw LLM stream messages (truncated, see §10.5) |
| `run:{run_id}:scratchpad` | hash | 7d | Shared key-value dict for multi-agent coordination |
| `channel:run:{run_id}` | pubsub | — | Live event stream for UI (ephemeral) |

### 5.2 Data Retention

All run data has a **7-day TTL** in Redis by default. OTel traces are the long-term data store — Redis is for live UI and short-term debugging. Stream recordings have a shorter **24-hour TTL** due to their size.

TTLs are set when a run completes (or fails). In-progress runs have no TTL — they expire only after completion + TTL duration. The default can be overridden per-workflow:

```python
@workflow(
    name="research-pipeline",
    redis_ttl_days=14,                  # override default 7d retention
    stream_ttl_hours=48,                # override default 24h stream retention
)
```

### 5.2 Checkpointing & Recovery

After each node completes successfully, its output is written to a checkpoint key in Redis. On crash recovery:

1. The executor loads `run:{run_id}:state` and identifies the last run status.
2. For each node, it checks for a checkpoint. Nodes with checkpoints are skipped (their stored output is used).
3. Execution resumes from the first un-checkpointed node.

```python
# Resume a failed run:
from workgraph import resume

await resume(run_id="abc123")
```

**Idempotency contract**: Nodes should be idempotent or at least tolerant of re-execution. The framework guarantees at-least-once with checkpoint deduplication — not exactly-once.

### 5.3 Shared Scratchpad (Multi-Agent Coordination)

For multi-agent patterns where agents need loose coupling, the scratchpad provides a shared `dict` backed by a Redis hash:

```python
@node(id="researcher")
async def researcher(topic: str, ctx: Context):
    findings = await ctx.llm(prompt=f"Research {topic}")
    await ctx.scratchpad.set("findings", findings)
    return findings

@node(id="critic")
async def critic(ctx: Context):
    findings = await ctx.scratchpad.get("findings")
    critique = await ctx.llm(prompt=f"Critique: {findings}")
    await ctx.scratchpad.set("critique", critique)
    return critique
```

The scratchpad is scoped per-run. Reads and writes are atomic at the key level (Redis `HGET`/`HSET`). For patterns that need stronger coordination, the framework exposes `ctx.scratchpad.cas(key, expected, new)` for compare-and-swap.

---

## 6. Observability (OpenTelemetry)

### 6.1 Span Hierarchy

Every workflow run creates a root span. Each node execution is a child span. LLM calls within nodes are nested spans under the node.

```
trace: research-pipeline (run_id=abc123)
 ├─ span: fetch_urls            [200ms]  status=OK
 ├─ span: scrape_pages          [1.2s]   status=OK
 ├─ span: parallel_group                 
 │   ├─ span: summarize[0]      [800ms]  status=OK
 │   ├─ span: summarize[1]      [650ms]  status=OK
 │   └─ span: summarize[2]      [1.1s]   status=ERROR (retry 1→OK)
 │       ├─ span: llm.complete  [720ms]  status=ERROR validation
 │       └─ span: llm.complete  [380ms]  status=OK (retry)
 ├─ span: synthesize            [1.5s]   status=OK
 └─ span: publish               [100ms]  status=OK
```

### 6.2 Span Attributes

**Node spans**:

| Attribute | Example |
|---|---|
| `workgraph.node.id` | `"summarize"` |
| `workgraph.node.attempt` | `1` |
| `workgraph.node.status` | `"ok"` / `"error"` / `"retry"` |
| `workgraph.validation.passed` | `true` / `false` |
| `workgraph.validation.errors` | `[{"field": "confidence", ...}]` |

**LLM spans**:

| Attribute | Example |
|---|---|
| `llm.model` | `"anthropic/claude-sonnet-4-20250514"` |
| `llm.provider` | `"anthropic"` |
| `llm.tokens.input` | `1200` |
| `llm.tokens.output` | `340` |
| `llm.cost.usd` | `0.0023` |
| `llm.latency_ms` | `720` |

### 6.3 Trace Export

Standard OTLP export — compatible with Jaeger, Grafana Tempo, Honeycomb, Datadog, etc. Configured at the workflow level:

```python
@workflow(
    name="research-pipeline",
    otel_endpoint="http://localhost:4317",    # OTLP gRPC
    otel_service_name="workgraph",
)
```

---

## 7. Web UI

### 7.1 Serving Model

The UI is **embedded in the FastAPI process**. Static assets (JS/CSS) are bundled and served from a `/ui` route. No separate frontend build step is required for production use.

```python
from workgraph import create_app

app = create_app(
    workflows=[research_pipeline],
    redis_url="redis://localhost:6379/0",
)

# Run with: uvicorn app:app --reload
```

### 7.2 Core Views

1. **Graph canvas** (litegraph.js): Nodes as boxes, edges as connections. Live coloring by status:
   - Gray: pending
   - Blue pulse: running
   - Green: completed
   - Red: failed
   - Amber: retrying

   Every node displays **live counters** when processing more than one item (`✓ 31  ▸ 12  ✗ 2  ◦ 2`). For single-item nodes (`total: 1`), the counters are hidden and the node shows a simple status color. This means the graph looks clean for simple linear pipelines and automatically becomes richer for fan-out-heavy workflows — no special "compound node" type needed.

   Nodes with active `ctx.progress` calls additionally show a progress bar within the node box.

2. **Node inspector panel**: Click any node to see its input, output, logs, trace spans, validation result, and error records. When a node processed multiple items, the inspector shows a scrollable **item list** with per-item status, output, and errors (fetched on-demand from the REST API).

3. **Stream panel**: When a node is running, the inspector shows a live-streaming text view of the current LLM call's output — chain-of-thought tokens appear with a short delay as they're generated. For multi-item nodes, the stream panel shows a tab per active item (up to `concurrency` tabs). Once the call completes, the stream content is replaced by the final structured output. Past stream content is available in the run's trace data.

3. **Error log panel**: Chronological list of all `NodeError` records for the current run. Each entry shows the node, item index, error type, structured detail, and a link to the corresponding OTel span. This panel shows the same data agents access via `ctx.get_errors()`, providing full transparency into what the agentic supervisor sees.

4. **Run timeline**: Horizontal waterfall showing wall-clock timing per node (derived from OTel spans). Multi-item nodes show as a single bar spanning from first-item-start to last-item-end.

5. **Run history**: List of past runs with status, duration, total LLM cost, error count.

### 7.3 Live Updates

WebSocket connection from UI → FastAPI. On each node state change, the executor publishes an event to Redis pubsub channel `channel:run:{run_id}`. FastAPI subscribes and forwards to connected WS clients.

**Event schema**:

```json
{
  "event": "node_status",
  "run_id": "abc123",
  "node_id": "summarize",
  "status": "running",
  "attempt": 1,
  "timestamp": "2026-04-07T14:30:00Z",
  "data": {}
}
```

Event types: `node_status`, `node_output`, `node_error`, `node_counters`, `node_progress`, `node_stream`, `node_stream_end`, `run_status`, `validation_result`.

**Streaming events** deliver LLM token chunks to the UI in real time:

```json
{
  "event": "node_stream",
  "run_id": "abc123",
  "node_id": "summarize_0",
  "item_index": 3,
  "chunk": "The key finding is that ",
  "stream_id": "llm_call_7f3a",
  "done": false,
  "timestamp": "2026-04-07T14:30:02.150Z"
}
```

When the LLM call completes, a final `node_stream` event is sent with `"done": true` and no chunk. The `stream_id` groups chunks belonging to the same LLM call (a node may make multiple LLM calls per item). Stream events are debounced server-side by `stream_delay_ms` — chunks arriving within the debounce window are concatenated into a single event to avoid flooding the WebSocket.

---

## 8. Deployment & Scaling

### 8.1 Phase 1: Single Process (Default)

Everything runs in one Python process using `asyncio`:

```
┌─────────────────────────────────┐
│  uvicorn (single process)       │
│  ├─ FastAPI (API + UI)          │
│  ├─ Executor (asyncio tasks)    │
│  └─ OTel SDK                    │
│           │                     │
│           ▼                     │
│       Redis (external)          │
└─────────────────────────────────┘
```

### 8.2 Phase 2: Distributed Workers (Future)

The executor is swapped for a task queue. Nodes are dispatched to worker processes on separate machines:

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  API server  │     │  Worker 1    │     │  Worker 2    │
│  (FastAPI)   │     │  (executor)  │     │  (executor)  │
└──────┬───────┘     └──────┬───────┘     └──────┬───────┘
       │                    │                    │
       ▼                    ▼                    ▼
  ┌─────────────────────────────────────────────────┐
  │                    Redis                        │
  │  (state + checkpoints + task queue + pubsub)    │
  └─────────────────────────────────────────────────┘
```

The transition requires no changes to workflow code — only deployment configuration.

---

## 9. API Surface Summary

### 9.1 Decorators

| Decorator | Purpose |
|---|---|
| `@node(...)` | Declare a graph node |
| `@workflow(...)` | Declare a workflow entry point |

### 9.2 Node Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `id` | `str` | function name | Unique node identifier |
| `retries` | `int` | `0` | Node-level retries (re-run entire node with full input list) |
| `item_retries` | `int` | `0` | Item-level retries (re-run single failed item) |
| `timeout` | `int \| None` | `None` | Seconds before timeout (per item) |
| `output_schema` | `BaseModel` | `None` | Pydantic model for output validation |
| `on_validation_fail` | `str` | `"retry"` | `"retry"` / `"fallback"` / `"fail"` (item-level) |
| `fallback_value` | `Any` | `None` | Value returned on fallback |
| `concurrency` | `int \| None` | `None` | Max parallel items (`None` = unlimited, `1` = sequential) |

### 9.3 Context Object

| Method / Property | Description |
|---|---|
| `ctx.llm(...)` | Provider-agnostic LLM call (litellm). Streams to UI by default; pass `stream=False` to disable. |
| `ctx.progress(desc=...)` | tqdm-compatible progress wrapper (also emits WS events) |
| `ctx.scratchpad.get(key)` | Read from shared scratchpad |
| `ctx.scratchpad.set(key, value)` | Write to shared scratchpad |
| `ctx.scratchpad.cas(key, expected, new)` | Compare-and-swap |
| `ctx.get_errors()` | Get all errors in current run |
| `ctx.get_errors(node_id=...)` | Get errors for a specific node |
| `ctx.has_errors()` | Boolean — any errors recorded so far? |
| `ctx.run_id` | Current run ID |
| `ctx.node_id` | Current node ID |
| `ctx.item_index` | Current item index within the node's input list |
| `ctx.logger` | Structured logger (auto-tagged with run/node/item) |

### 9.4 Composition Functions

| Function | Description |
|---|---|
| `race(tasks)` | Fan-out, first-wins, cancel rest |
| `merge(a, b)` | Concatenate two node output lists |
| `resume(run_id)` | Resume a failed run from last checkpoint |

---

## 10. Streaming Output

### 10.1 Chain-of-Thought Streaming

Nodes can stream their LLM output to the UI in real time, allowing observers to watch chain-of-thought reasoning as it happens. Streaming is enabled by using `ctx.llm(..., stream=True)`:

```python
@node(id="reason")
async def reason(question: str, ctx: Context) -> ReasoningOutput:
    result = await ctx.llm(
        prompt=f"Think step by step: {question}",
        response_model=ReasoningOutput,
        stream=True,                          # enables token streaming to UI
    )
    return result
```

The framework handles token streaming automatically: each chunk from the LLM provider is forwarded to the UI via a WebSocket event, then the final accumulated output is validated against `output_schema` as usual.

### 10.2 Streaming with Display Delay

To make chain-of-thought traces readable in the UI (rather than a blur of tokens), a configurable **display delay** is applied UI-side. This does not slow down execution — tokens are buffered on the client and rendered with a staggered animation.

```python
@workflow(
    name="research-pipeline",
    stream_delay_ms=50,             # UI rendering delay between tokens (default: 30)
)
```

### 10.3 WebSocket Stream Events

```json
{
  "event": "node_stream",
  "run_id": "abc123",
  "node_id": "reason_0",
  "item_index": 0,
  "token": "Let me think about",
  "timestamp": "2026-04-07T14:30:01.234Z"
}
```

```json
{
  "event": "node_stream_end",
  "run_id": "abc123",
  "node_id": "reason_0",
  "item_index": 0,
  "timestamp": "2026-04-07T14:30:03.456Z"
}
```

### 10.4 UI Behavior

When a node is streaming, the inspector panel shows a live text area with tokens appearing in real time. For multi-item nodes, the inspector shows the currently selected item's stream (selectable via the item list). The graph canvas node shows a subtle typing indicator (animated ellipsis) alongside the normal counter badges.

### 10.5 Stream Recording & Truncation

Stream data is recorded to Redis as a list of JSON messages, giving short-term debuggability without the cost of permanent storage. OTel traces are the long-term data store.

**Storage**: Each node+item stream is stored as a Redis list at `run:{run_id}:node:{node_id}:stream`. Each entry is a valid JSON message object:

```json
{"index": 0, "token": "Let me", "ts": 1712505001234}
{"index": 1, "token": " think about", "ts": 1712505001267}
{"index": 2, "token": " this step", "ts": 1712505001301}
```

**Truncation**: When the stream exceeds a configurable message limit (`stream_max_messages`, default 500), the list is truncated to keep only the **last N messages**. Because each entry is a self-contained JSON object, truncation never produces invalid data — the list simply starts later in the stream. A metadata entry is prepended to indicate truncation:

```json
{"_truncated": true, "original_count": 1847, "kept": 500}
```

**TTL**: Stream recordings have a **24-hour TTL** in Redis (configurable via `stream_ttl_hours`). After expiry, the raw stream is gone — the final validated output remains in the node's checkpoint (7-day TTL), and the full response is available in the OTel span (`llm.raw_response`) for as long as your trace backend retains it.

**Configuration**:

```python
@workflow(
    name="research-pipeline",
    stream_delay_ms=50,              # UI rendering delay (default: 30)
    stream_max_messages=500,         # truncation threshold (default: 500)
    stream_ttl_hours=24,             # Redis TTL for stream data (default: 24)
)
```

---

## 11. Workflow Versioning

### 11.1 Version Identity

Every workflow has a **version hash** derived from its source code. When the workflow code changes, the version changes, and all run state (checkpoints, status, errors) belongs to the old version — the new version starts clean.

```python
# Version hash is computed automatically from:
# - The workflow function source code
# - All @node function sources referenced by the workflow
# - The decorator parameters (retries, schemas, concurrency, etc.)
# - The workflow-level config (default_model, etc.)

version = sha256(
    workflow_source + node_sources + decorator_configs + workflow_config
)[:12]    # e.g. "a3f8c1d04b2e"
```

### 11.2 Redis Key Scoping

All run state keys are scoped by workflow version:

| Key pattern | Purpose |
|---|---|
| `wf:{name}:version` | Current active version hash |
| `wf:{name}:versions` | Set of all known version hashes |
| `run:{run_id}:version` | Version hash this run was created under |

When the framework starts and detects a new version:

1. Compute the version hash from current source code.
2. Compare against `wf:{name}:version` in Redis.
3. If changed: store the new version, add to the version set, log the transition. New runs use the new version. Old runs remain queryable but cannot be resumed.

### 11.3 Checkpoint Compatibility

Checkpoints are **not compatible across versions**. Attempting to `resume()` a run whose version differs from the current workflow version raises a `VersionMismatchError`:

```python
try:
    await resume(run_id="abc123")
except VersionMismatchError as e:
    # e.run_version = "a3f8c1d04b2e"
    # e.current_version = "7f2b9e1c8d3a"
    print(f"Cannot resume: run was version {e.run_version}, "
          f"workflow is now {e.current_version}")
```

### 11.4 Run History & Version Filtering

The UI's run history view groups runs by version. Users can filter to see only runs from the current version or browse historical versions. The version hash is displayed alongside each run, and version transitions are highlighted in the timeline.

### 11.5 API

```python
from workgraph import get_version, list_versions

current = get_version("research-pipeline")         # "7f2b9e1c8d3a"
all_versions = list_versions("research-pipeline")   # ["a3f8c1d04b2e", "7f2b9e1c8d3a"]
```

---

## 12. Testing

Testing is a first-class concern. The framework ships with `workgraph.testing` — providing mock LLMs, graph snapshot assertions, and full trace replay. All tests run with no external dependencies (no Redis server, no LLM API keys).

### 12.1 Mock LLMs

The `MockLLM` replaces `ctx.llm` in tests with deterministic, scriptable responses:

```python
from workgraph.testing import MockLLM, run_test

mock = MockLLM()

# Script responses by node ID
mock.on("summarize").respond(SummaryOutput(
    summary="Test summary",
    confidence=0.95,
    key_topics=["testing"],
))

# Sequential responses (for testing retries or multi-item nodes)
mock.on("summarize").respond_sequence([
    {"summary": "ok", "confidence": "high", "key_topics": []},   # invalid → retry
    SummaryOutput(summary="ok", confidence=0.9, key_topics=[]),   # valid
])

# Simulate failures
mock.on("fetch_urls").raise_error(TimeoutError("connection timed out"))

# Dynamic responses
mock.on("classify").respond_with(lambda prompt, **kw: classify_logic(prompt))

# Run the workflow with the mock
result = await run_test(research_pipeline, llm=mock)

# Assertions
assert mock.call_count("summarize") == 2       # retried once
assert "rejected by validation" in mock.last_call("summarize").prompt  # feedback injected
```

**MockLLM API**:

| Method | Description |
|---|---|
| `on(node_id)` | Scope responses to a specific node |
| `.respond(value)` | Return a fixed value |
| `.respond_sequence([...])` | Return values in order |
| `.respond_with(callable)` | Dynamic response based on prompt/input |
| `.raise_error(exc)` | Simulate failures |
| `call_count(node_id)` | Number of LLM calls for a node |
| `last_call(node_id)` | Most recent call (prompt, model, params, response) |
| `all_calls(node_id)` | Full call history |

### 12.2 Graph Snapshot Testing

Snapshot tests verify that the traced graph structure hasn't changed unexpectedly. This catches accidental regressions in workflow topology:

```python
from workgraph.testing import assert_graph_snapshot

def test_research_pipeline_graph():
    # First run: creates snapshot at tests/snapshots/research_pipeline.graph.json
    # Subsequent runs: asserts the graph matches the stored snapshot
    assert_graph_snapshot(
        research_pipeline,
        snapshot_path="tests/snapshots/research_pipeline.graph.json",
    )
```

**Snapshot file format**:

```json
{
  "workflow": "research-pipeline",
  "version": "7f2b9e1c8d3a",
  "nodes": [
    {
      "instance_id": "fetch_urls_0",
      "node_id": "fetch_urls",
      "depends_on": [],
      "output_schema": "UrlList",
      "retries": 0,
      "item_retries": 2,
      "concurrency": null
    }
  ],
  "edges": [
    ["fetch_urls_0", "scrape_pages_0"]
  ]
}
```

Update intentionally: `pytest tests/ --update-snapshots`. The diff is visible in version control.

### 12.3 Trace Replay Testing

Replay tests record a real workflow run (inputs, LLM calls, outputs) and re-execute the workflow using the recorded LLM responses as a mock. This catches regressions in node logic, prompt construction, and validation behavior.

**Recording**:

```python
from workgraph.testing import record_trace

trace = await record_trace(research_pipeline, inputs={"query": "agentic frameworks"})
trace.save("tests/traces/research_golden.trace.json")
```

The recording captures every node and item: input, raw LLM prompt, raw LLM response, validated output, and errors. LLM calls are the recording boundary — everything else is deterministic given the same inputs and responses.

**Replaying**:

```python
from workgraph.testing import replay_trace

result = await replay_trace(
    research_pipeline,
    trace_path="tests/traces/research_golden.trace.json",
    mode="strict",        # "strict" | "structural" | "inputs_only"
)
assert result.all_passed()
```

**Replay modes**:

| Mode | Behavior |
|---|---|
| `"strict"` (default) | Assert all outputs match exactly. Fail on any divergence. |
| `"structural"` | Assert graph shape and execution order match, allow output differences. |
| `"inputs_only"` | Replay with recorded LLM responses but don't assert outputs — just verify no crashes. |

**Divergence reporting**: When the workflow has changed since recording, replay reports exactly what diverged:

```
REPLAY DIVERGENCE:
  ✓ fetch_urls_0     input=match  output=match
  ✓ scrape_pages_0   input=match  output=match
  ✗ summarize_0      input=match  output=DIVERGED
    recorded: {"summary": "Old summary", ...}
    replayed: {"summary": "New format", ...}
    cause: prompt template changed
  ⊘ new_node_0       not in recording (skipped)
```

### 12.4 Test Context & Fixtures

The `test_context()` factory creates a fully functional `Context` backed by in-memory fakes — no external Redis needed:

```python
from workgraph.testing import MockLLM, TestRedis, run_test, test_context

# Test a single node in isolation
async def test_summarize_node():
    mock = MockLLM()
    mock.on("summarize").respond(SummaryOutput(
        summary="Test", confidence=0.9, key_topics=["a"]
    ))
    ctx = test_context(llm=mock)
    result = await summarize("some text", ctx)
    assert result.confidence == 0.9

# Test a full workflow
async def test_full_pipeline():
    mock = MockLLM()
    mock.on("fetch_urls").respond(["https://example.com"])
    mock.on("scrape_pages").respond(Page(text="hello"))
    mock.on("summarize").respond(SummaryOutput(...))
    mock.on("synthesize").respond(Report(...))

    result = await run_test(research_pipeline, llm=mock)
    assert len(result) == 1

# Test error recovery
async def test_supervisor_receives_errors():
    mock = MockLLM()
    mock.on("task_a").raise_error(ValueError("boom"))
    mock.on("supervisor").respond(RecoveryPlan(action="skip"))

    ctx = test_context(
        llm=mock,
        errors=[NodeError(node_id="task_a", error_type="exception", ...)],
    )
    result = await supervisor(ctx)
    assert result.action == "skip"

# Capture and assert WebSocket events
async def test_events_emitted():
    mock = MockLLM()
    mock.on("summarize").respond(SummaryOutput(...))

    events = []
    await run_test(research_pipeline, llm=mock, on_event=events.append)

    counter_events = [e for e in events if e["event"] == "node_counters"]
    assert any(e["counters"]["completed"] > 0 for e in counter_events)
```

### 12.5 Property-Based Testing

The framework includes Hypothesis strategies for generating random workflow topologies and verifying executor invariants. This catches edge cases that scripted tests miss — deadlocks, ordering violations, checkpoint corruption, and concurrency bugs.

```python
from hypothesis import given, settings
from workgraph.testing.strategies import (
    workflow_graphs,        # generates random DAGs of mock nodes
    item_lists,             # generates random input lists of varying length
    failure_scenarios,      # injects random failures at random points
)

@given(graph=workflow_graphs(max_nodes=20, max_edges=40))
@settings(max_examples=200)
async def test_executor_never_deadlocks(graph):
    """No valid DAG should cause the executor to hang."""
    mock = MockLLM()
    mock.on_any().respond({"result": "ok"})

    result = await run_test(graph.as_workflow(), llm=mock, timeout=10)
    assert result.status in ("completed", "failed")    # never "running" after timeout


@given(
    graph=workflow_graphs(max_nodes=10),
    failures=failure_scenarios(),
)
async def test_checkpoint_consistency(graph, failures):
    """After crash + resume, the final output matches a clean run."""
    mock = MockLLM()
    mock.on_any().respond({"result": "ok"})

    # Run with injected crash
    crashed = await run_test(
        graph.as_workflow(), llm=mock,
        crash_after_node=failures.crash_point,
    )

    # Resume from checkpoint
    resumed = await resume(crashed.run_id)

    # Compare against a clean run
    clean = await run_test(graph.as_workflow(), llm=mock)
    assert resumed.outputs == clean.outputs


@given(items=item_lists(min_size=1, max_size=100))
async def test_concurrency_bound_respected(items):
    """Node with concurrency=3 never has more than 3 items running."""
    concurrency_log = []

    @node(id="tracked", concurrency=3)
    async def tracked(x: int, ctx: Context) -> int:
        concurrency_log.append(("start", ctx.item_index))
        await asyncio.sleep(0.01)
        concurrency_log.append(("end", ctx.item_index))
        return x

    await run_test_node(tracked, items=items)

    # Verify max concurrent was never > 3
    active = 0
    max_active = 0
    for event, _ in concurrency_log:
        active += 1 if event == "start" else -1
        max_active = max(max_active, active)
    assert max_active <= 3


@given(graph=workflow_graphs(max_nodes=15))
async def test_topological_order(graph):
    """Every node executes only after all its dependencies have completed."""
    execution_order = []

    mock = MockLLM()
    mock.on_any().respond_with(lambda **kw: (
        execution_order.append(kw["node_id"]),
        {"result": "ok"},
    )[1])

    await run_test(graph.as_workflow(), llm=mock)

    for node in graph.nodes:
        node_pos = execution_order.index(node.instance_id)
        for dep in node.depends_on:
            dep_pos = execution_order.index(dep)
            assert dep_pos < node_pos, (
                f"{node.instance_id} ran before its dependency {dep}"
            )
```

**Built-in strategies**:

| Strategy | Generates |
|---|---|
| `workflow_graphs(max_nodes, max_edges)` | Random valid DAGs with mock node definitions |
| `item_lists(min_size, max_size)` | Random input lists for testing fan-out |
| `failure_scenarios()` | Random crash points, timeout durations, validation errors |
| `concurrency_configs()` | Random concurrency/retry/timeout combinations |

**Key invariants tested**:

- **No deadlocks**: Every valid DAG completes or fails within a bounded time.
- **Topological order**: No node executes before its dependencies.
- **Checkpoint consistency**: Crash + resume produces the same output as a clean run.
- **Concurrency bounds**: `concurrency=N` never allows more than N concurrent items.
- **Error recording completeness**: Every failed item produces exactly one `NodeError`.
- **Counter accuracy**: Final counter values match actual item outcomes.

### 12.6 CI Integration

All testing utilities run in CI with zero external dependencies:

- `TestRedis` — in-memory Redis implementation, no server needed
- `MockLLM` — no API calls, no credentials needed
- Snapshot files — committed to version control, diffed in PRs
- Trace files — can be large; store in CI artifacts or `.gitignore` with a fixture that downloads on demand
- Property-based tests — run with `--hypothesis-seed` for reproducibility

```yaml
# Example GitHub Actions step
- name: Run workgraph tests
  run: |
    pytest tests/ -v --tb=short
    pytest tests/ --update-snapshots --dry-run  # verify no unintended changes
    pytest tests/property/ --hypothesis-seed=0  # reproducible property tests
```

---

## 13. Decisions Log

Resolved decisions tracked here for reference:

| Decision | Resolution | Section |
|---|---|---|
| UI editing model | Read-only observer, no bidirectional sync | §1 |
| Multi-agent patterns | All composable: pipeline, scratchpad, message passing | §5.3 |
| Deployment model | Single-process default, distributed future | §8 |
| LLM interface | Provider-agnostic via litellm | §3.2 |
| Validation recovery | Configurable per-node: retry, fallback, fail | §4.2–4.3 |
| Human-in-the-loop | No — fully autonomous, errors for agentic review | §4.4 |
| Crash recovery | Checkpoint to Redis, resume from last successful node | §5.3 |
| Multi-agent coordination | Shared dict in Redis (scratchpad) | §5.4 |
| UI serving | Embedded in FastAPI process | §7.1 |
| Graph derivation | Eager tracing (dry-run with proxy objects) | §3.4 |
| Dynamic topology | Yes — list length drives fan-out at runtime | §3.4.3 |
| Node execution model | Every node is a map (list-in/list-out) | §4.1 |
| Retry model | Split: `retries` (node-level) + `item_retries` (item-level) | §4.2 |
| Streaming | Yes, with UI-side display delay | §10 |
| Stream recording | Truncate to last N messages, 24h TTL, OTel for long-term | §10.5 |
| Testing | Day-one: mock LLMs, graph snapshots, replay, property-based | §12 |
| Workflow versioning | Status resets per version, checkpoints incompatible across versions | §11 |
| Error log retention | 7-day TTL in Redis | §5.2 |
| Graph visualization | litegraph.js until proven otherwise | §7.2 |
| Cost budgets | Not in v1 | — |
| Version diffing in UI | Not needed | — |
| Recording privacy/redaction | Not in v1 | — |
