from __future__ import annotations

import asyncio
import json
import queue
import time

from workgraph import node, workflow
from workgraph.core import Executor
from workgraph.store import RedisStore
from workgraph.testing import MockLLM


class FakePubSub:
    def __init__(self, redis: "FakeRedis") -> None:
        self.redis = redis
        self.channels: set[str] = set()
        self.messages: queue.Queue[dict] = queue.Queue()
        self.closed = False

    def subscribe(self, channel: str) -> None:
        self.channels.add(channel)
        self.redis.subscribers.setdefault(channel, []).append(self)

    def get_message(self, timeout: float = 0.0):
        if self.closed:
            return None
        try:
            return self.messages.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self.closed = True
        for channel in list(self.channels):
            subscribers = self.redis.subscribers.get(channel, [])
            if self in subscribers:
                subscribers.remove(self)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.sets: dict[str, set[str]] = {}
        self.expirations: dict[str, int] = {}
        self.subscribers: dict[str, list[FakePubSub]] = {}

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

    def expire(self, key: str, ttl_seconds: int) -> None:
        self.expirations[key] = ttl_seconds

    def publish(self, channel: str, payload: str) -> None:
        message = {"type": "message", "channel": channel, "data": payload}
        for subscriber in list(self.subscribers.get(channel, [])):
            subscriber.messages.put(message)

    def pubsub(self, ignore_subscribe_messages: bool = True):
        return FakePubSub(self)


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
    assert store.get_run("redis-run").status == "completed"
    assert [entry["token"] for entry in store.get_stream("redis-run", "redis_llm_0", 0)] == ["x"]
    span_names = [span["name"] for span in store.get_spans("redis-run")]
    assert "redis-flow" in span_names
    assert "llm.complete" in span_names
    assert fake.expirations["run:redis-run"] == 7 * 24 * 3600
    assert fake.expirations["run:redis-run:events"] == 7 * 24 * 3600
    assert fake.expirations["run:redis-run:spans"] == 7 * 24 * 3600
    assert fake.expirations["run:redis-run:node:redis_llm_0:item:0:stream"] == 24 * 3600


def test_redis_store_pubsub_subscription(monkeypatch):
    fake = FakeRedis()

    class FakeRedisFactory:
        @staticmethod
        def from_url(url: str, decode_responses: bool = True):
            return fake

    import redis

    monkeypatch.setattr(redis, "Redis", FakeRedisFactory)

    store = RedisStore("redis://example/0")

    async def exercise() -> None:
        store.publish_event("pubsub-run", {"event": "run_status", "run_id": "pubsub-run", "status": "running"})
        queue_handle = store.subscribe("pubsub-run")
        history = await asyncio.wait_for(queue_handle.get(), timeout=0.2)
        assert history["status"] == "running"

        store.publish_event("pubsub-run", {"event": "node_status", "run_id": "pubsub-run", "node_id": "n0"})
        live = await asyncio.wait_for(queue_handle.get(), timeout=0.5)
        assert live["event"] == "node_status"
        assert json.loads(fake.lists["run:pubsub-run:events"][1])["node_id"] == "n0"
        store.unsubscribe("pubsub-run", queue_handle)

    asyncio.run(exercise())
