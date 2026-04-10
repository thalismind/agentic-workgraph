# Example Library

The example library is meant to answer one question quickly: "What does an agentic workflow look like in this framework when it is doing real workflow-shaped work?"

## Workflow Set

### `example-hello`

The smallest useful example. One node, one item, one output. Use this when you only need to verify tracing, execution, and the UI shell.

### `example-fanout-research`

Shows the default list-in/list-out model:

- one upstream node returns multiple topics
- one LLM-backed node maps over those topics with concurrency
- one downstream node turns the structured summaries into a readable final output

This is the clearest example of the framework's default "fan-out without a parallel primitive" design.

### `example-conditional-review`

Shows conditional topology with `trace_branches="all"`.

- a draft node creates an answer
- a review node produces a structured decision
- the graph includes both the revise and no-revise paths

Execution still follows the primary trace plan, but the graph view exposes both conditional branches.

### `example-iterative-refinement`

Shows loop modeling.

- a seed draft node starts the work
- a refinement node is invoked repeatedly
- the graph collapses those repeated calls into one loop node with iteration metadata

This is the pattern to reach for when you want bounded refinement instead of open-ended recursion.

### `example-scratchpad-collaboration`

Shows coordination through the per-run scratchpad.

- one node writes findings
- one node critiques them
- one final node reads shared context back out

This is the closest thing to a multi-agent collaboration pattern in the current v1 shape.

### `example-subgraph-child` and `example-subgraph-parent`

Shows workflow composition where a parent workflow launches a child workflow as a real linked run.

- the parent graph renders the child workflow as one subgraph node
- the child workflow still has its own run history, graph, trace spans, and artifact
- the node inspector can navigate directly into the child run

This is the reference example for reusable workflow components that should stay debuggable with the existing UI.

### `example-live-weather-capture`

Shows a real-world workflow that crosses the boundary from in-memory orchestration into external effects.

- one node fetches live weather data over HTTP from a public API
- one node launches a real browser and captures a screenshot of a weather site
- the workflow writes the screenshot to disk and returns an artifact summary

This is the example to use when you want to prove the framework can coordinate actual I/O instead of only mock or LLM-shaped work.
