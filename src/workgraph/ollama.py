from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, request

from .models import StreamEnvelope

_DEFAULT_LOCAL_BASE_URL = "http://localhost:11434"
_DEFAULT_CLOUD_BASE_URL = "https://ollama.com"
_DOTENV_PATH = Path.home() / ".env"
_GENERATE_FIELDS = {
    "context",
    "format",
    "images",
    "keep_alive",
    "options",
    "raw",
    "suffix",
    "system",
    "template",
    "think",
}


@dataclass(slots=True)
class OllamaConfig:
    base_url: str
    api_key: str | None = None
    default_model: str | None = None
    timeout: float = 120.0
    headers: dict[str, str] | None = None
    options: dict[str, Any] | None = None
    think: bool | str | None = None
    keep_alive: str | int | None = None
    format: str | dict[str, Any] | None = None


def _strip_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def _strip_model_prefix(model: str) -> str:
    return model.split("/", 1)[1] if model.startswith("ollama/") else model


def _strip_cloud_suffix(model: str) -> str:
    return model[:-6] if model.endswith(":cloud") else model


def _request_headers(config: OllamaConfig) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if config.headers:
        headers.update(config.headers)
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    return headers


def _request_payload(config: OllamaConfig, *, prompt: str, stream: bool, model: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": _strip_model_prefix(model),
        "prompt": prompt,
        "stream": stream,
    }
    if config.options:
        payload["options"] = dict(config.options)
    if config.think is not None:
        payload["think"] = config.think
    if config.keep_alive is not None:
        payload["keep_alive"] = config.keep_alive
    if config.format is not None:
        payload["format"] = config.format
    for key in _GENERATE_FIELDS:
        if key in kwargs and kwargs[key] is not None:
            if key == "options" and "options" in payload:
                payload["options"] = {**payload["options"], **kwargs["options"]}
            else:
                payload[key] = kwargs[key]
    return payload


def _parse_error_body(exc: error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8").strip()
    except Exception:  # noqa: BLE001
        body = ""
    detail = f": {body}" if body else ""
    return f"Ollama request failed with status {exc.code}{detail}"


def _load_dotenv_key(env_var: str) -> str | None:
    if not _DOTENV_PATH.exists():
        return None
    try:
        for line in _DOTENV_PATH.read_text().splitlines():
            if line.startswith(f"{env_var}="):
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                return value or None
    except Exception:  # noqa: BLE001
        return None
    return None


def _resolve_cloud_api_key(api_key: str | None) -> str | None:
    if api_key:
        return api_key
    for env_var in ("OLLAMA_API_KEY", "OLLAMA_CLOUD_API_KEY"):
        value = os.getenv(env_var)
        if value:
            return value.strip('"').strip("'")
    for env_var in ("OLLAMA_API_KEY", "OLLAMA_CLOUD_API_KEY"):
        value = _load_dotenv_key(env_var)
        if value:
            return value
    return None


def _generate(config: OllamaConfig, *, prompt: str, stream: bool, model: str, kwargs: dict[str, Any]) -> str | StreamEnvelope:
    payload = _request_payload(config, prompt=prompt, stream=stream, model=model, kwargs=kwargs)
    body = json.dumps(payload).encode("utf-8")
    http_request = request.Request(
        f"{_strip_base_url(config.base_url)}/api/generate",
        data=body,
        headers=_request_headers(config),
        method="POST",
    )
    try:
        with request.urlopen(http_request, timeout=config.timeout) as response:  # noqa: S310
            if not stream:
                data = json.loads(response.read().decode("utf-8"))
                return data.get("response", "")
            tokens: list[str] = []
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                chunk = json.loads(line)
                token = chunk.get("response")
                if token:
                    tokens.append(token)
                if chunk.get("done"):
                    break
            text = "".join(tokens)
            return StreamEnvelope(tokens=tokens, response=text)
    except error.HTTPError as exc:
        raise RuntimeError(_parse_error_body(exc)) from exc
    except error.URLError as exc:
        raise RuntimeError(f"Ollama request failed: {exc.reason}") from exc


def create_ollama_llm(
    *,
    base_url: str = _DEFAULT_LOCAL_BASE_URL,
    api_key: str | None = None,
    model: str | None = None,
    timeout: float = 120.0,
    headers: dict[str, str] | None = None,
    options: dict[str, Any] | None = None,
    think: bool | str | None = None,
    keep_alive: str | int | None = None,
    format: str | dict[str, Any] | None = None,
):
    config = OllamaConfig(
        base_url=base_url,
        api_key=api_key,
        default_model=model,
        timeout=timeout,
        headers=headers,
        options=options,
        think=think,
        keep_alive=keep_alive,
        format=format,
    )

    async def ollama_llm(*, prompt: str, node_id: str, stream: bool = True, **kwargs: Any) -> Any:
        resolved_model = kwargs.pop("model", None) or config.default_model
        if not resolved_model:
            raise RuntimeError(f"No Ollama model configured for node '{node_id}'")
        return await asyncio.to_thread(
            _generate,
            config,
            prompt=prompt,
            stream=stream,
            model=resolved_model,
            kwargs=kwargs,
        )

    return ollama_llm


def create_ollama_cloud_llm(
    *,
    api_key: str | None = None,
    base_url: str = _DEFAULT_CLOUD_BASE_URL,
    model: str | None = None,
    timeout: float = 120.0,
    headers: dict[str, str] | None = None,
    options: dict[str, Any] | None = None,
    think: bool | str | None = None,
    keep_alive: str | int | None = None,
    format: str | dict[str, Any] | None = None,
):
    resolved_api_key = _resolve_cloud_api_key(api_key)
    if not resolved_api_key:
        raise RuntimeError(
            "Ollama Cloud API key not found (checked OLLAMA_API_KEY, OLLAMA_CLOUD_API_KEY, ~/.env)"
        )
    return create_ollama_llm(
        base_url=base_url,
        api_key=resolved_api_key,
        model=_strip_cloud_suffix(model) if model else None,
        timeout=timeout,
        headers=headers,
        options=options,
        think=think,
        keep_alive=keep_alive,
        format=format,
    )
