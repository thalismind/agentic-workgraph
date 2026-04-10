# Agentic Documentation

This framework is strongest when you treat workflows as explicit coordination structures, not as hidden chains of prompts.

## Core Patterns

### Pipeline

Use a plain series of nodes when each step depends directly on the previous one and the data shape stays narrow.

### Fan-out / fan-in

Use list outputs to create parallel work naturally.

- upstream returns `list[T]`
- downstream scalar node is mapped over each item
- a later node can aggregate the resulting list back into one output

This is the default pattern for research, enrichment, classification, and summarization.

### Conditional routing

Use workflow-level Python control flow when the graph shape depends on an intermediate decision.

For observability, prefer `trace_branches="all"` when branch visibility matters in the debugger.

### Iterative refinement

Use a bounded loop when the same operation should improve the result over several passes. The runtime now models repeated self-dependent calls as one loop node in the graph while preserving per-iteration execution internally.

### Subgraph composition

Use `run_subgraph(...)` when one workflow should orchestrate another workflow as a reusable component.

- the parent graph shows one subgraph node
- the child workflow gets its own real run record, history, traces, and artifact
- downstream parent nodes receive the child run's final output

This is the right pattern when the child flow is substantial enough to deserve its own debugger surface. If the work would be clearer as a few ordinary nodes in one graph, keep it flat.

### Scratchpad collaboration

Use `ctx.scratchpad` when steps need shared run-scoped state that should not be passed directly through every function signature.

This is useful for:

- shared findings
- critique and revision state
- agent-to-agent handoff metadata

### Supervisor and recovery

Use `ctx.get_errors()` and resume/checkpoint behavior when a later step needs to inspect failures and decide whether to retry, skip, degrade, or merge partial outputs.

### External effect workflows

When a workflow needs to touch the outside world, keep the effect explicit in the node boundary.

- fetch external state in one node
- transform or validate it in a later node
- write artifacts in a dedicated side-effect node

The `example-live-weather-capture` workflow is the reference pattern for combining network reads with filesystem artifacts.

## Design Guidance

- Keep node functions scalar. Let the runtime handle list mapping.
- Use output schemas on any node where an LLM is producing contract-shaped data.
- Prefer bounded loops and explicit branches over hidden retry logic in prompts.
- Use subgraphs for reusable workflow-sized coordination, with explicit pre/post mapping nodes when shapes differ.
- Treat the UI as a debugger for coordination structure, not just a run log.
- Reach for Redis when you want state durability or cross-process visibility.
