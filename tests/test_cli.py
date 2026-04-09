from __future__ import annotations

import json

from workgraph import cli


def test_parse_named_args_supports_json_scalars_and_flags():
    payload = cli._parse_named_args(
        ["--prompt-text=hello", "--count", "3", "--enabled", "--no-cache", "--items=[1,2]", "--name", "kimi-k2.5"]
    )

    assert payload == {
        "prompt_text": "hello",
        "count": 3,
        "enabled": True,
        "cache": False,
        "items": [1, 2],
        "name": "kimi-k2.5",
    }


def test_run_command_posts_kwargs_and_waits(monkeypatch, capsys):
    responses = [
        {"run_id": "run-123", "status": "pending", "workflow": "demo", "version": "v1"},
        {"run_id": "run-123", "workflow": "demo", "status": "running", "version": "v1", "nodes": {}},
        {"run_id": "run-123", "workflow": "demo", "status": "completed", "version": "v1", "nodes": {}},
    ]
    calls = []

    def fake_request_json(base_url: str, method: str, path: str, payload=None):
        calls.append((base_url, method, path, payload))
        return responses.pop(0)

    monkeypatch.setattr(cli, "_request_json", fake_request_json)
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)

    exit_code = cli.main(
        [
            "--base-url",
            "http://example.test",
            "run",
            "demo",
            "--wait",
            "--prompt-text=hello",
            "--dry-run",
        ]
    )

    assert exit_code == 0
    assert calls[0] == (
        "http://example.test",
        "POST",
        "/api/workflows/demo/runs",
        {"kwargs": {"prompt_text": "hello", "dry_run": True}},
    )
    assert calls[1][2] == "/api/runs/run-123"
    assert calls[2][2] == "/api/runs/run-123"
    assert "status=completed" in capsys.readouterr().out


def test_run_wait_artifact_fetches_final_artifact(monkeypatch, capsys):
    responses = [
        {"run_id": "run-123", "status": "pending", "workflow": "demo", "version": "v1"},
        {"run_id": "run-123", "workflow": "demo", "status": "completed", "version": "v1", "nodes": {}},
        {"run_id": "run-123", "workflow": "demo", "status": "completed", "artifact": {"value": 7}, "manifest": None},
    ]

    monkeypatch.setattr(cli, "_request_json", lambda *args, **kwargs: responses.pop(0))
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)

    exit_code = cli.main(["run", "demo", "--wait", "--artifact", "--prompt=hello"])

    assert exit_code == 0
    assert '"value": 7' in capsys.readouterr().out


def test_status_command_returns_failure_exit_code(monkeypatch, capsys):
    run = {"run_id": "run-9", "workflow": "demo", "status": "failed", "version": "v1", "nodes": {}}

    monkeypatch.setattr(cli, "_request_json", lambda *args, **kwargs: run)

    exit_code = cli.main(["status", "run-9"])

    assert exit_code == 1
    assert "status=failed" in capsys.readouterr().out


def test_status_artifact_fetches_artifact(monkeypatch, capsys):
    responses = [
        {"run_id": "run-9", "workflow": "demo", "status": "completed", "version": "v1", "nodes": {}},
        {"run_id": "run-9", "workflow": "demo", "status": "completed", "artifact": {"ok": True}, "manifest": {"next": "x"}},
    ]

    monkeypatch.setattr(cli, "_request_json", lambda *args, **kwargs: responses.pop(0))

    exit_code = cli.main(["status", "run-9", "--artifact"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert '"ok": true' in output
    assert '"next": "x"' in output


def test_artifact_command_fetches_past_run_artifact(monkeypatch, capsys):
    payload = {"run_id": "run-7", "workflow": "demo", "status": "completed", "artifact": {"value": "done"}, "manifest": None}

    monkeypatch.setattr(cli, "_request_json", lambda *args, **kwargs: payload)

    exit_code = cli.main(["artifact", "run-7"])

    assert exit_code == 0
    assert '"value": "done"' in capsys.readouterr().out


def test_workflows_command_supports_json_output(monkeypatch, capsys):
    workflows = [{"name": "demo", "current_version": "v1", "run_count": 2, "latest_run": {"status": "completed"}}]

    monkeypatch.setattr(cli, "_request_json", lambda *args, **kwargs: workflows)

    exit_code = cli.main(["--json", "workflows"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == workflows


def test_launch_spec_command_formats_expected_inputs(monkeypatch, capsys):
    payload = {
        "workflow": "demo",
        "params": [
            {
                "name": "prompt_text",
                "kind": "POSITIONAL_OR_KEYWORD",
                "required": True,
                "default": None,
                "annotation": "<class 'str'>",
            },
            {
                "name": "dry_run",
                "kind": "POSITIONAL_OR_KEYWORD",
                "required": False,
                "default": False,
                "annotation": "<class 'bool'>",
            },
        ],
    }

    monkeypatch.setattr(cli, "_request_json", lambda *args, **kwargs: payload)

    exit_code = cli.main(["launch-spec", "demo"])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "workflow=demo" in output
    assert "prompt_text (required, POSITIONAL_OR_KEYWORD) -> <class 'str'>" in output
    assert "dry_run (optional, POSITIONAL_OR_KEYWORD) -> <class 'bool'> default=false" in output


def test_launch_spec_command_supports_json_output(monkeypatch, capsys):
    payload = {"workflow": "demo", "params": [{"name": "prompt_text", "required": True}]}

    monkeypatch.setattr(cli, "_request_json", lambda *args, **kwargs: payload)

    exit_code = cli.main(["--json", "launch-spec", "demo"])

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == payload
