from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from workgraph import Executor, create_ollama_cloud_llm, create_ollama_llm, node, workflow


class _OllamaHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests: list[dict] = []
    stream_tokens = ["Sera", "phyne"]
    response_text = "Seraphyne"

    def do_POST(self):  # noqa: N802
        length = int(self.headers["Content-Length"])
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        self.__class__.requests.append(
            {
                "path": self.path,
                "headers": dict(self.headers.items()),
                "payload": payload,
            }
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        if payload.get("stream", True):
            self.end_headers()
            for token in self.__class__.stream_tokens:
                self.wfile.write(json.dumps({"response": token, "done": False}).encode("utf-8") + b"\n")
                self.wfile.flush()
            self.wfile.write(json.dumps({"response": "", "done": True}).encode("utf-8") + b"\n")
            self.wfile.flush()
            return
        body = json.dumps({"response": self.__class__.response_text, "done": True}).encode("utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A003
        return


class _OllamaServer:
    def __init__(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _OllamaHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __enter__(self):
        _OllamaHandler.requests = []
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


@node(id="ask_ollama")
async def ask_ollama(topic: str, ctx):
    return await ctx.llm(prompt=f"Write a title for {topic}", model="ollama/gemma3")


@workflow(name="ollama-flow")
def ollama_flow():
    return ask_ollama(topic=["dark angel"])


async def test_ollama_adapter_streams_generate_responses():
    with _OllamaServer() as server:
        executor = Executor(llm_callable=create_ollama_llm(base_url=server.base_url))
        run = await executor.run(ollama_flow)

    assert run.status == "completed"
    assert run.final_output == ["Seraphyne"]
    stream_events = [event for event in executor.store.event_history[run.run_id] if event["event"] == "node_stream"]
    assert [event["token"] for event in stream_events] == ["Sera", "phyne"]
    request_payload = _OllamaHandler.requests[-1]["payload"]
    assert request_payload["model"] == "gemma3"
    assert request_payload["prompt"] == "Write a title for dark angel"
    assert request_payload["stream"] is True


async def test_ollama_cloud_adapter_sends_bearer_auth():
    with _OllamaServer() as server:
        llm = create_ollama_cloud_llm(base_url=server.base_url, api_key="secret", model="kimi-k2.5:cloud")
        response = await llm(prompt="Why?", node_id="cloud-node", stream=False)

    assert response == "Seraphyne"
    request_record = _OllamaHandler.requests[-1]
    assert request_record["path"] == "/api/generate"
    assert request_record["headers"]["Authorization"] == "Bearer secret"
    assert request_record["payload"]["model"] == "kimi-k2.5"
