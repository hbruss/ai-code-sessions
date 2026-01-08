"""Tests for core parsing pipelines and legacy metadata."""

import json

import click

import ai_code_sessions.core as core


def test_parse_session_file_json(tmp_path):
    payload = {"loglines": [{"type": "user", "message": {"content": "hi"}}]}
    path = tmp_path / "session.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = core.parse_session_file(path)

    assert result["loglines"][0]["type"] == "user"


def test_parse_claude_jsonl_preserves_compact_summary(tmp_path):
    path = tmp_path / "session.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "user",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {"role": "user", "content": "Hello"},
                "isCompactSummary": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = core.parse_session_file(path)

    assert result["source_format"] == "claude_jsonl"
    assert result["loglines"][0]["isCompactSummary"] is True


def test_parse_codex_rollout_jsonl(tmp_path):
    path = tmp_path / "rollout.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-01-01T00:00:00Z",
                        "type": "session_meta",
                        "payload": {
                            "id": "sess-1",
                            "timestamp": "2026-01-01T00:00:00Z",
                            "cwd": "/project",
                            "originator": "codex_cli",
                            "cli_version": "1.0",
                            "git": {
                                "commit_hash": "abc1234",
                                "branch": "main",
                                "repository_url": "git@github.com:acme/repo.git",
                            },
                        },
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-01-01T00:00:01Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "Hi"}],
                        },
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-01-01T00:00:02Z",
                        "type": "response_item",
                        "payload": {
                            "type": "function_call",
                            "name": "Bash",
                            "call_id": "call-1",
                            "arguments": '{"command": "ls"}',
                        },
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-01-01T00:00:03Z",
                        "type": "response_item",
                        "payload": {
                            "type": "function_call_output",
                            "call_id": "call-1",
                            "output": "Process exited with code 1\nOops",
                        },
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-01-01T00:00:04Z",
                        "type": "response_item",
                        "payload": {
                            "type": "reasoning",
                            "summary": [{"type": "summary_text", "text": "Thoughts"}],
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = core.parse_session_file(path)

    assert result["source_format"] == "codex_rollout"
    assert result["meta"]["git"]["repository_url"].endswith("acme/repo.git")
    loglines = result["loglines"]
    assert any(block.get("type") == "tool_use" for block in loglines[1]["message"]["content"])
    assert loglines[2]["message"]["content"][0]["is_error"] is False
    assert loglines[3]["message"]["content"][0]["type"] == "thinking"


def test_find_best_source_file_invalid_timestamp():
    try:
        core.find_best_source_file(
            tool="codex",
            cwd="/tmp",
            project_root="/tmp",
            start="not-a-date",
            end="2026-01-01T00:00:00Z",
        )
    except click.ClickException as exc:
        assert "Invalid --start/--end timestamp" in str(exc)
    else:
        raise AssertionError("Expected ClickException")


def test_legacy_ctx_metadata_extracts_resume_id(tmp_path):
    session_dir = tmp_path / "legacy"
    session_dir.mkdir()

    messages = {
        "started": "2026-01-01T00:00:00Z",
        "ended": "2026-01-01T01:00:00Z",
        "label": "Legacy",
        "tool": "codex",
        "project_root": "/repo",
        "cwd": "/repo",
        "messages": [{"text": "codex resume 123e4567-e89b-12d3-a456-426614174000"}],
    }
    (session_dir / "messages.json").write_text(json.dumps(messages), encoding="utf-8")

    (session_dir / "events.jsonl").write_text(
        json.dumps({"type": "event", "ts": "2026-01-01T00:00:00Z", "cwd": "/repo"})
        + "\n"
        + json.dumps({"type": "event", "ts": "2026-01-01T01:00:00Z", "cwd": "/repo"})
        + "\n",
        encoding="utf-8",
    )

    meta = core._legacy_ctx_metadata(session_dir)

    assert meta is not None
    assert meta["codex_resume_id"] == "123e4567-e89b-12d3-a456-426614174000"
