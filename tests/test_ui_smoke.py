from __future__ import annotations

import asyncio
import socket
import threading
import time
from contextlib import closing

import httpx
import pytest
import uvicorn

from workgraph import create_app, node, workflow


@node(id="hello")
async def hello(ctx, name: str):
    return f"hello {name}"


@workflow(name="hello-flow")
def hello_flow():
    return hello(name=["world"])


def _find_free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def _wait_for_server(base_url: str, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = httpx.get(f"{base_url}/api/workflows", timeout=0.5)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"server did not start at {base_url}")


@pytest.fixture()
def live_ui_server():
    playwright = pytest.importorskip("playwright.sync_api")
    app = create_app(workflows=[hello_flow])
    asyncio.run(app.state.executor.run(hello_flow, run_id="ui-smoke-run-a"))
    asyncio.run(app.state.executor.run(hello_flow, run_id="ui-smoke-run-b"))

    port = _find_free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    _wait_for_server(base_url)

    try:
        yield base_url, playwright
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def test_ui_hash_deeplink_and_run_switching(live_ui_server):
    base_url, playwright_module = live_ui_server
    with playwright_module.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()

        page.goto(f"{base_url}/ui#workflow=hello-flow&run=ui-smoke-run-a", wait_until="networkidle")
        page.wait_for_function(
            "document.getElementById('detail-title')?.textContent?.trim() === 'ui-smoke-run-a'"
        )
        assert "workflow=hello-flow" in page.evaluate("window.location.hash")
        assert "run=ui-smoke-run-a" in page.evaluate("window.location.hash")

        page.get_by_text("ui-smoke-run-b", exact=True).click()
        page.wait_for_function(
            "document.getElementById('detail-title')?.textContent?.trim() === 'ui-smoke-run-b'"
        )
        assert "run=ui-smoke-run-b" in page.evaluate("window.location.hash")

        page.reload(wait_until="networkidle")
        page.wait_for_function(
            "document.getElementById('detail-title')?.textContent?.trim() === 'ui-smoke-run-b'"
        )
        assert page.locator("#history-title").text_content() == "hello-flow"
        assert page.locator("#detail-title").text_content() == "ui-smoke-run-b"

        browser.close()
