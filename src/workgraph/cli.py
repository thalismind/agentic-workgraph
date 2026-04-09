from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any
from urllib import error, request


DEFAULT_BASE_URL = os.getenv("WORKGRAPH_BASE_URL", "http://127.0.0.1:8081")


class CliError(RuntimeError):
    pass


def _strip_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def _request_json(base_url: str, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    http_request = request.Request(
        f"{_strip_base_url(base_url)}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with request.urlopen(http_request, timeout=30) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        if detail:
            raise CliError(f"API request failed with status {exc.code}: {detail}") from exc
        raise CliError(f"API request failed with status {exc.code}") from exc
    except error.URLError as exc:
        raise CliError(f"Could not reach workgraph API at {base_url}: {exc.reason}") from exc


def _parse_scalar(value: str) -> Any:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _parse_named_args(tokens: list[str]) -> dict[str, Any]:
    kwargs: dict[str, Any] = {}
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not token.startswith("--"):
            raise CliError(f"Unexpected argument '{token}'. Named workflow args must start with '--'.")
        name = token[2:]
        if not name:
            raise CliError("Empty named argument.")
        if "=" in name:
            raw_name, raw_value = name.split("=", 1)
            kwargs[raw_name.replace("-", "_")] = _parse_scalar(raw_value)
            index += 1
            continue
        if name.startswith("no-"):
            kwargs[name[3:].replace("-", "_")] = False
            index += 1
            continue
        normalized = name.replace("-", "_")
        next_token = tokens[index + 1] if index + 1 < len(tokens) else None
        if next_token is None or next_token.startswith("--"):
            kwargs[normalized] = True
            index += 1
            continue
        kwargs[normalized] = _parse_scalar(next_token)
        index += 2
    return kwargs


def _dump(payload: Any, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    if isinstance(payload, str):
        print(payload)
        return
    print(json.dumps(payload, indent=2, sort_keys=True))


def _format_workflows(workflows: list[dict[str, Any]]) -> str:
    lines = []
    for workflow in workflows:
        latest = workflow.get("latest_run")
        latest_summary = latest["status"] if isinstance(latest, dict) else "no runs"
        lines.append(
            f"{workflow['name']}  version={workflow['current_version']}  runs={workflow['run_count']}  latest={latest_summary}"
        )
    return "\n".join(lines)


def _format_runs(runs: list[dict[str, Any]]) -> str:
    lines = []
    for run in runs:
        lines.append(
            f"{run['run_id']}  workflow={run['workflow']}  status={run['status']}  version={run['version']}"
        )
    return "\n".join(lines)


def _format_status(run: dict[str, Any]) -> str:
    lines = [
        f"run_id={run['run_id']}",
        f"workflow={run['workflow']}",
        f"status={run['status']}",
        f"version={run['version']}",
    ]
    if run.get("final_node_id"):
        lines.append(f"final_node_id={run['final_node_id']}")
    node_states = run.get("nodes", {})
    if node_states:
        lines.append("nodes:")
        for node_id, state in node_states.items():
            lines.append(f"  {node_id}: {state['status']}")
    return "\n".join(lines)


def _format_artifact(payload: dict[str, Any]) -> str:
    lines = [
        f"run_id={payload['run_id']}",
        f"workflow={payload['workflow']}",
        f"status={payload['status']}",
        "artifact:",
        json.dumps(payload.get("artifact"), indent=2, sort_keys=True),
    ]
    manifest = payload.get("manifest")
    if manifest is not None:
        lines.extend(["manifest:", json.dumps(manifest, indent=2, sort_keys=True)])
    return "\n".join(lines)


def _print_run_artifact(base_url: str, run_id: str, *, as_json: bool) -> None:
    artifact = _request_json(base_url, "GET", f"/api/runs/{run_id}/artifact")
    _dump(artifact, as_json=as_json) if as_json else print(_format_artifact(artifact))


def _wait_for_run(base_url: str, run_id: str, *, poll_interval: float) -> dict[str, Any]:
    while True:
        payload = _request_json(base_url, "GET", f"/api/runs/{run_id}")
        if payload["status"] in {"completed", "failed"}:
            return payload
        time.sleep(poll_interval)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workgraph")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Workgraph API base URL.")
    parser.add_argument("--json", action="store_true", help="Print JSON output.")

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("workflows", help="List workflows exposed by the API.")
    subparsers.add_parser("list", help="Alias for workflows.")

    runs_parser = subparsers.add_parser("runs", help="List recent runs.")
    runs_parser.add_argument("--workflow", help="Filter runs by workflow name.")
    runs_parser.add_argument("--version", help="Filter runs by workflow version.")
    runs_parser.add_argument("--limit", type=int, help="Limit the number of runs shown.")

    run_parser = subparsers.add_parser("run", help="Launch a workflow run.")
    run_parser.add_argument("workflow", help="Workflow name to launch.")
    run_parser.add_argument("--wait", action="store_true", help="Wait for the run to finish.")
    run_parser.add_argument("--artifact", action="store_true", help="Print the final artifact after waiting for completion.")
    run_parser.add_argument("--poll-interval", type=float, default=1.0, help="Polling interval in seconds when waiting.")

    status_parser = subparsers.add_parser("status", help="Fetch a run record.")
    status_parser.add_argument("run_id", help="Run ID to inspect.")
    status_parser.add_argument("--watch", action="store_true", help="Poll until the run completes.")
    status_parser.add_argument("--artifact", action="store_true", help="Print the final artifact instead of the run summary.")
    status_parser.add_argument("--poll-interval", type=float, default=1.0, help="Polling interval in seconds when watching.")

    artifact_parser = subparsers.add_parser("artifact", help="Fetch the final artifact for a run.")
    artifact_parser.add_argument("run_id", help="Run ID to inspect.")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args, extra = parser.parse_known_args(argv)

    try:
        if args.command in {"workflows", "list"}:
            workflows = _request_json(args.base_url, "GET", "/api/workflows")
            _dump(workflows, as_json=args.json) if args.json else print(_format_workflows(workflows))
            return 0

        if args.command == "runs":
            query = []
            if args.workflow:
                query.append(f"workflow={args.workflow}")
            if args.version:
                query.append(f"version={args.version}")
            suffix = f"?{'&'.join(query)}" if query else ""
            runs = _request_json(args.base_url, "GET", f"/api/runs{suffix}")
            if args.limit is not None:
                runs = runs[: args.limit]
            _dump(runs, as_json=args.json) if args.json else print(_format_runs(runs))
            return 0

        if args.command == "run":
            kwargs = _parse_named_args(extra)
            if args.artifact and not args.wait:
                raise CliError("--artifact requires --wait on the run command.")
            payload = _request_json(
                args.base_url,
                "POST",
                f"/api/workflows/{args.workflow}/runs",
                payload={"kwargs": kwargs},
            )
            if not args.wait:
                _dump(payload, as_json=args.json) if args.json else print(
                    f"run_id={payload['run_id']} status={payload['status']} workflow={payload['workflow']}"
                )
                return 0
            final = _wait_for_run(args.base_url, payload["run_id"], poll_interval=args.poll_interval)
            if args.artifact:
                _print_run_artifact(args.base_url, payload["run_id"], as_json=args.json)
            else:
                _dump(final, as_json=args.json) if args.json else print(_format_status(final))
            return 0 if final["status"] == "completed" else 1

        if args.command == "status":
            run = _wait_for_run(args.base_url, args.run_id, poll_interval=args.poll_interval) if args.watch else _request_json(
                args.base_url, "GET", f"/api/runs/{args.run_id}"
            )
            if args.artifact:
                _print_run_artifact(args.base_url, args.run_id, as_json=args.json)
            else:
                _dump(run, as_json=args.json) if args.json else print(_format_status(run))
            return 0 if run["status"] != "failed" else 1

        if args.command == "artifact":
            _print_run_artifact(args.base_url, args.run_id, as_json=args.json)
            return 0

        raise CliError(f"Unknown command '{args.command}'")
    except CliError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
