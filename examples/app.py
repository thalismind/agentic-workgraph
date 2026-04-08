from __future__ import annotations

from workgraph import create_app
from workgraph.models import StreamEnvelope

from .workflows import EXAMPLE_WORKFLOWS, ReviewDecision, Summary


async def example_llm(*, prompt: str, node_id: str, node_instance_id: str, stream: bool = True, **kwargs):
    if node_id == "summarize_topic":
        topic = prompt.replace("Summarize ", "").strip()
        response = Summary(
            summary=f"{topic} works best when graph state and observability stay aligned.",
            confidence=0.92,
        ).model_dump(mode="json")
        if stream:
            return StreamEnvelope(
                tokens=[f"{topic} ", "works ", "best ", "when graph state and observability stay aligned."],
                response=response,
            )
        return response
    if node_id == "review_answer":
        decision = ReviewDecision(approved=True, feedback="Approved with stronger tracing language.")
        return decision.model_dump(mode="json")
    raise RuntimeError(f"No example LLM response configured for node '{node_id}' ({node_instance_id})")


app = create_app(workflows=EXAMPLE_WORKFLOWS)
app.state.executor.llm_callable = example_llm
