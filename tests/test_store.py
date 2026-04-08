from __future__ import annotations

from workgraph import node, workflow
from workgraph.core import Executor
from workgraph.store import RedisStore
from workgraph.testing import MockLLM


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.sets: dict[str, set[str]] = {}

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def get(self, key: str):
        return self.values.get(key)

    def sadd(self, key: str, value: str) -> None:
        self.sets.setdefault(key, set()).add(value)

    def smembers(self, key: str):
        return self.sets.get(key, set())

    def rpush(self, key: str, value: str) -> None:
        self.lists.setdefault(key, []).append(value)

    def lrange(self, key: str, start: int, end: int):
        values = self.lists.get(key, [])
        if end == -1:
            end = len(values) - 1
        return values[start : end + 1]

    def keys(self, pattern: str):
        if pattern == "run:*":
            return [key for key in self.values if key.startswith("run:") and key.count(":") == 1]
        return []


@node(id="redis_llm")
async def redis_llm(value: str, ctx):
    return await ctx.llm(prompt=f"value {value}")


@workflow(name="redis-flow")
def redis_flow():
    return redis_llm(value=["x"])


def test_redis_store_round_trip(monkeypatch):
    fake = FakeRedis()

    class FakeRedisFactory:
        @staticmethod
        def from_url(url: str, decode_responses: bool = True):
            return fake

    import redis

    monkeypatch.setattr(redis, "Redis", FakeRedisFactory)

    store = RedisStore("redis://example/0")
    executor = Executor(store=store, llm_callable=MockLLM())
    executor.llm_callable.on("redis_llm").stream(["x"], "done")

    import asyncio

    run = asyncio.run(executor.run(redis_flow, run_id="redis-run"))

    assert run.status == "completed"
    assert store.get_run("redis-run").run_id == "redis-run"
    assert [entry["token"] for entry in store.get_stream("redis-run", "redis_llm_0", 0)] == ["x"]
    span_names = [span["name"] for span in store.get_spans("redis-run")]
    assert "redis-flow" in span_names
    assert "llm.complete" in span_names
