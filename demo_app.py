from pydantic import BaseModel

from workgraph import create_app, node, workflow
from workgraph.models import StreamEnvelope
from examples.workflows import iterative_refinement


@node(id="hello")
async def hello(name: str, ctx):
    return f"hello {name}"


@workflow(name="hello-flow")
def hello_flow():
    return hello(name=["world"])


class Summary(BaseModel):
    summary: str
    confidence: float


@node(id="fetch_topics")
async def fetch_topics(seed: str, ctx):
    return [f"{seed} systems", f"{seed} orchestration", f"{seed} tracing"]


@node(id="summarize_topic", output_schema=Summary, concurrency=2)
async def summarize_topic(topic: str, ctx):
    async with ctx.progress(desc="summarizing") as progress:
        await progress.update(0.35)
        result = await ctx.llm(prompt=f"Summarize {topic}", response_model=Summary, stream=True)
        await progress.update(0.65)
    return result


@node(id="synthesize_report")
async def synthesize_report(summary: Summary, ctx):
    return f"{summary.summary} ({summary.confidence:.2f})"


@workflow(name="research-demo")
def research_demo():
    topics = fetch_topics(seed=["agentic"])
    summaries = summarize_topic(topic=topics)
    return synthesize_report(summary=summaries)


async def demo_llm(*, prompt: str, node_id: str, node_instance_id: str, stream: bool = True, **kwargs):
    topic = prompt.replace("Summarize ", "").strip()
    response = {
        "summary": f"{topic} benefits from code-first orchestration.",
        "confidence": 0.91,
    }
    if stream:
        return StreamEnvelope(
            tokens=[
                f"{topic} ",
                "benefits ",
                "from ",
                "code-first orchestration.",
            ],
            response=response,
        )
    return response


app = create_app(workflows=[hello_flow, research_demo, iterative_refinement])
app.state.executor.llm_callable = demo_llm
