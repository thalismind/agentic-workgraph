# Downstream Integration Guide

This guide is for teams integrating `agentic-workgraph` into a real project.

The workflow-authoring guide explains how to write nodes and workflows. This document explains how to embed them into a project with real prompts, real fixtures, real deployment, and a clean package layout.

## Goal

A downstream integration should end up with:

- a project-local workflow package
- one shared registry of workflows
- one shared FastAPI app entrypoint
- curated fixtures and prompt files near the workflows
- app-level LLM wiring
- a debugger UI that reflects the project’s real workflows

A downstream workflow package should be treated as the reference implementation for its own project.

## Recommended Layout

Use a dedicated package or directory for workflows inside the downstream repo.

Example:

```text
my-project/
  workflows/
    __init__.py
    app.py
    registry.py
    content_prep.py
    release_prep.py
    training_prep.py
    publish_prep.py
    pi_identity.py
    README.md
    test_drafts/
```

Recommended roles:

- `registry.py`: the canonical list of workflows to expose
- `app.py`: the shared `create_app(...)` entrypoint
- `*.py` workflow modules: project-local logic
- `test_drafts/` or similar: normalized fixtures for stable execution
- `README.md`: package-specific usage notes

Avoid one-off app modules for every workflow. A shared registry and app surface scale much better.

## Keep Project Logic Local

Do not try to force every downstream concern back into the core library.

Keep these things in the downstream project:

- prompt text and persona files
- fixture normalization rules
- content schemas that are specific to the project
- filesystem paths to drafts, release folders, or datasets
- external tool invocations that belong to the project

Keep these things in `agentic-workgraph`:

- runtime behavior
- stores
- tracing
- UI shell
- generic testing helpers
- reusable adapters like Ollama

If a feature only exists to support one project’s historical data, it probably belongs in the project package, not the core library.

## Registry Pattern

Use one registry module as the source of truth for exposed workflows.

Example:

```python
from .content_prep import content_prep
from .publish_prep import publish_prep
from .release_prep import release_prep

WORKFLOWS = [
    content_prep,
    release_prep,
    publish_prep,
]
```

This gives you one place to:

- see what is live
- control workflow ordering in the UI
- avoid forgotten workflow modules that exist on disk but are not registered

## Shared App Pattern

Build one shared FastAPI app for the downstream project.

Example:

```python
from workgraph import create_app, create_ollama_cloud_llm

from .registry import WORKFLOWS

app = create_app(
    workflows=WORKFLOWS,
    llm_callable=create_ollama_cloud_llm(model="kimi-k2.5:cloud"),
)
```

Why this pattern matters:

- all workflows share the same API and `/ui`
- model wiring stays consistent
- deployment is one process, not many one-off launch scripts

This is a good default shape for running on `8081`.

## Prompt Files

Keep large prompts as files, not giant inline strings in Python.

Use prompt files for:

- system prompts
- editorial instructions
- platform policies
- reusable critique frames

Benefits:

- easier review in git
- easier reuse across workflows
- cleaner Python modules

If a workflow invokes an external agent harness, pass the prompt in from a tracked file. For example, keep a prompt like `workflows/system-prompt.md` in the repo and feed that to the `pi` harness from the workflow node.

## Fixture Strategy

Use curated fixtures, not the entire historical corpus, as the live execution input set.

Preferred pattern:

1. copy a few recent real documents into a curated fixture directory
2. normalize them to the new shape
3. use those files for workflow defaults and tests
4. treat the larger historical archive as reference material until it is repaired

Important rule:

- if old drafts are malformed, fix the JSON rather than widening the code to support every old shape

For content-like documents:

- `notes` should be at least an array
- field names should be stable
- optional metadata should remain optional instead of shape-shifting

This keeps the workflow logic focused on real decisions instead of archaeology.

## Real Data Boundaries

When a workflow touches real project data, keep the boundaries explicit.

Good examples:

- load one release folder
- load one normalized scheduled draft
- load one dataset-build candidate
- invoke one external harness with a tracked prompt

Bad examples:

- “scan the whole repo and guess what to do”
- “accept every historical shape ever seen”
- “publish directly from whatever was loaded”

If the integration touches a real external system, make the effect a dedicated node and keep pre-publish and publish workflows separate.

## LLM Wiring

Prefer app-level model injection rather than building a model client inside every workflow module.

That gives you:

- one provider choice per deployment
- easy swapping between local Ollama and Ollama Cloud
- simpler tests

Current recommended baseline:

```python
from workgraph import create_ollama_cloud_llm

llm_callable = create_ollama_cloud_llm(model="kimi-k2.5:cloud")
```

Use Ollama local when:

- latency matters more than quality
- the model is available on the host
- you want a local-only development path

Use Ollama Cloud when:

- you want a consistent hosted baseline
- the workflow needs a stronger remote model
- you are standardizing evaluation and review behavior across machines

## External Tools And Harnesses

If the downstream project already has tools like `pi`, launchers, or custom scripts, wrap them in workflow nodes instead of treating them as invisible background behavior.

Rules:

- build the command explicitly
- capture stdout, stderr, exit code, and duration
- return a structured terminal artifact
- keep secrets in the environment, never in the returned payload
- prefer a tracked prompt file over inline shell quoting

This is the correct way to make those tools visible in the debugger and testable through the API.

## Deployment Shape

A downstream app should be launchable with one Uvicorn command.

Example:

```bash
.venv/bin/python -m uvicorn \
  --app-dir my-project/workflows \
  app:app \
  --host 0.0.0.0 \
  --port 8081
```

That gives you:

- `/api/workflows`
- `/api/runs/...`
- `/ui`

This should be the main operational surface for the downstream workflow package.

## Testing Expectations

A downstream workflow package should test three things:

1. registration
2. execution
3. fixture contracts

Recommended checks:

- the shared app exposes the expected workflows
- a default run completes against curated fixtures
- final artifacts have the expected shape
- fixture normalization assumptions stay true over time

If a workflow invokes an external tool, add at least one smoke test against a controlled prompt or fixture.

## UI Verification

Once the package is wired into the shared app, verify the actual downstream workflows in `/ui`.

For each new workflow, check:

- it appears in the workflow list
- it launches cleanly from the UI
- progress and item records make sense
- final artifacts foreground the useful output
- the graph shape is readable
- trace and errors are understandable

If the workflow is long-running or streams output, verify that websocket updates arrive before the run completes.

## Migration Guidance

If you are bringing an older project into `agentic-workgraph`, migrate in slices:

1. choose one safe pre-publish workflow
2. normalize a few real fixtures
3. build the smallest useful graph
4. wire one real LLM step
5. verify in `/ui`
6. add more workflows only after the package shape feels stable

Do not start with direct publishing or broad historical compatibility.

## Example Reference

A downstream package can demonstrate these patterns:

- curated normalized draft fixtures
- one shared app entrypoint
- one registry module
- hosted critique nodes wired through the shared app LLM
- project-local prompt files
- a workflow that invokes the real `pi` harness and captures its output

That is a good model to follow unless there is a strong project-specific reason to diverge.
