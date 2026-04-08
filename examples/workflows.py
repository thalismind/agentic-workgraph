from __future__ import annotations

from pydantic import BaseModel

from workgraph import node, workflow


class Summary(BaseModel):
    summary: str
    confidence: float


class ReviewDecision(BaseModel):
    approved: bool
    feedback: str


@node(id="hello")
async def hello(name: str, ctx):
    return f"hello {name}"


@workflow(name="example-hello")
def hello_flow():
    return hello(name=["world"])


@node(id="fetch_topics")
async def fetch_topics(seed: str, ctx):
    return [f"{seed} orchestration", f"{seed} tracing", f"{seed} recovery"]


@node(id="summarize_topic", output_schema=Summary, concurrency=2)
async def summarize_topic(topic: str, ctx):
    return await ctx.llm(prompt=f"Summarize {topic}", response_model=Summary, stream=True)


@node(id="synthesize_brief")
async def synthesize_brief(summary: Summary, ctx):
    return f"{summary.summary} ({summary.confidence:.2f})"


@workflow(name="example-fanout-research")
def fanout_research():
    topics = fetch_topics(seed=["agentic"])
    summaries = summarize_topic(topic=topics)
    return synthesize_brief(summary=summaries)


@node(id="draft_answer")
async def draft_answer(question: str, ctx):
    return f"Draft answer for: {question}"


@node(id="review_answer", output_schema=ReviewDecision)
async def review_answer(answer: str, ctx):
    return await ctx.llm(prompt=f"Review {answer}", response_model=ReviewDecision, stream=False)


@node(id="revise_answer")
async def revise_answer(answer: str, ctx):
    return f"{answer} (revised)"


@workflow(name="example-conditional-review", trace_branches="all")
def conditional_review():
    answer = draft_answer(question=["How should agents recover from failure?"])
    decision = review_answer(answer=answer)
    if decision:
        return revise_answer(answer=answer)
    return answer


@node(id="seed_draft")
async def seed_draft(topic: str, ctx):
    return f"Initial draft about {topic}"


@node(id="refine_draft")
async def refine_draft(draft: str, ctx):
    async with ctx.progress(desc="refining") as progress:
        await progress.update(1.0)
    return f"{draft} -> refined"


@workflow(name="example-iterative-refinement", max_loop_iterations=4)
def iterative_refinement():
    draft = seed_draft(topic=["loop modeling"])
    for _ in range(3):
        draft = refine_draft(draft=draft)
    return draft


@node(id="research_findings")
async def research_findings(topic: str, ctx):
    findings = [f"{topic} needs observability", f"{topic} benefits from checkpoints"]
    await ctx.scratchpad.set("findings", findings)
    return findings


@node(id="critic_findings")
async def critic_findings(findings: str, ctx):
    critique = f"Critique: {findings}"
    await ctx.scratchpad.set("critique", critique)
    return critique


@node(id="final_recommendation")
async def final_recommendation(critique: str, ctx):
    findings = await ctx.scratchpad.get("findings")
    return f"{critique} | findings={findings}"


@workflow(name="example-scratchpad-collaboration")
def scratchpad_collaboration():
    findings = research_findings(topic=["agent review"])
    critique = critic_findings(findings=findings)
    return final_recommendation(critique=critique)


EXAMPLE_WORKFLOWS = [
    hello_flow,
    fanout_research,
    conditional_review,
    iterative_refinement,
    scratchpad_collaboration,
]
