from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus
from urllib.request import urlopen

from pydantic import BaseModel

from workgraph import node, workflow


class Summary(BaseModel):
    summary: str
    confidence: float


class ReviewDecision(BaseModel):
    approved: bool
    feedback: str


class WeatherObservation(BaseModel):
    location: str
    latitude: float
    longitude: float
    temperature_c: float
    apparent_temperature_c: float
    wind_speed_kph: float
    weather_code: int
    observed_at: str
    screenshot_url: str


class WeatherCapture(BaseModel):
    location: str
    temperature_c: float
    screenshot_url: str
    screenshot_path: str
    screenshot_bytes: int


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


def _fetch_json(url: str) -> dict:
    with urlopen(url, timeout=20) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


@node(id="fetch_live_weather", output_schema=WeatherObservation)
async def fetch_live_weather(location: str, ctx):
    encoded_location = quote_plus(location)

    def get_weather() -> WeatherObservation:
        geocode = _fetch_json(
            f"https://geocoding-api.open-meteo.com/v1/search?name={encoded_location}&count=1&language=en&format=json"
        )
        if not geocode.get("results"):
            raise RuntimeError(f"No geocoding results for '{location}'")
        result = geocode["results"][0]
        forecast = _fetch_json(
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={result['latitude']}&longitude={result['longitude']}"
            "&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m"
        )
        current = forecast["current"]
        return WeatherObservation(
            location=result["name"],
            latitude=result["latitude"],
            longitude=result["longitude"],
            temperature_c=current["temperature_2m"],
            apparent_temperature_c=current["apparent_temperature"],
            wind_speed_kph=current["wind_speed_10m"],
            weather_code=current["weather_code"],
            observed_at=current["time"],
            screenshot_url=f"https://wttr.in/{quote_plus(result['name'])}",
        )

    return await asyncio.to_thread(get_weather)


@node(id="capture_weather_site", output_schema=WeatherCapture)
async def capture_weather_site(observation: WeatherObservation, ctx, output_dir: str = "/tmp/agentic-workgraph-weather"):
    def capture() -> WeatherCapture:
        base_dir = Path(output_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_location = observation.location.lower().replace(" ", "-")
        screenshot_path = base_dir / f"{safe_location}-{ctx.run_id}-{ctx.item_index}-{timestamp}.png"
        subprocess.run(  # noqa: S603
            ["playwright", "screenshot", observation.screenshot_url, str(screenshot_path)],
            check=True,
            capture_output=True,
            text=True,
        )

        return WeatherCapture(
            location=observation.location,
            temperature_c=observation.temperature_c,
            screenshot_url=observation.screenshot_url,
            screenshot_path=str(screenshot_path),
            screenshot_bytes=screenshot_path.stat().st_size,
        )

    return await asyncio.to_thread(capture)


@node(id="summarize_weather_capture")
async def summarize_weather_capture(capture: WeatherCapture, ctx):
    return (
        f"{capture.location}: {capture.temperature_c:.1f}C "
        f"| screenshot={capture.screenshot_path} "
        f"| bytes={capture.screenshot_bytes}"
    )


@workflow(name="example-live-weather-capture")
def live_weather_capture(location: list[str] | None = None, output_dir: str = "/tmp/agentic-workgraph-weather"):
    weather = fetch_live_weather(location=location or ["Chicago"])
    capture = capture_weather_site(observation=weather, output_dir=output_dir)
    return summarize_weather_capture(capture=capture)


EXAMPLE_WORKFLOWS = [
    hello_flow,
    fanout_research,
    conditional_review,
    iterative_refinement,
    scratchpad_collaboration,
    live_weather_capture,
]
