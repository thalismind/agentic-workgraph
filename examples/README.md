# Example Workflows

This directory is a runnable library of small workflows that demonstrate the framework's main agentic patterns.

Run the example app:

```bash
.venv/bin/python -m uvicorn examples.app:app --host 0.0.0.0 --port 8081
```

Included workflows:

- `example-hello`: smallest possible end-to-end flow
- `example-fanout-research`: list fan-out, structured LLM outputs, and fan-in reporting
- `example-conditional-review`: conditional routing with `trace_branches="all"`
- `example-iterative-refinement`: loop modeling with a collapsed loop node in the graph
- `example-scratchpad-collaboration`: scratchpad-backed multi-step coordination
- `example-live-weather-capture`: real network fetch plus real browser screenshot written to disk
