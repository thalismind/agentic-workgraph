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

### Scratchpad collaboration

Use `ctx.scratchpad` when steps need shared run-scoped state that should not be passed directly through every function signature.

This is useful for:

- shared findings
- critique and revision state
- agent-to-agent handoff metadata

### Supervisor and recovery

Use `ctx.get_errors()` and resume/checkpoint behavior when a later step needs to inspect failures and decide whether to retry, skip, degrade, or merge partial outputs.

## Design Guidance

- Keep node functions scalar. Let the runtime handle list mapping.
- Use output schemas on any node where an LLM is producing contract-shaped data.
- Prefer bounded loops and explicit branches over hidden retry logic in prompts.
- Treat the UI as a debugger for coordination structure, not just a run log.
- Reach for Redis when you want state durability or cross-process visibility.
