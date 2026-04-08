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
