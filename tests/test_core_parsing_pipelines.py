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


def test_parse_omp_session_jsonl_normalizes_messages_and_skips_metadata(tmp_path):
    path = tmp_path / "omp-session.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "title",
                        "v": 1,
                        "title": "Synthetic OMP session",
                        "source": "auto",
                        "updatedAt": "2026-01-01T00:00:00Z",
                        "pad": "",
                    }
                ),
                json.dumps(
                    {
                        "type": "session",
                        "version": 3,
                        "id": "omp-session-1",
                        "timestamp": "2026-01-01T00:00:00Z",
                        "cwd": "/project",
                        "title": "Synthetic OMP session",
                    }
                ),
                json.dumps(
                    {
                        "type": "message",
                        "id": "user-1",
                        "parentId": None,
                        "timestamp": "2026-01-01T00:00:01Z",
                        "message": {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "Prompt for omp-session-1"},
                                {"type": "image", "data": "synthetic-base64", "mimeType": "image/png"},
                            ],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "message",
                        "id": "assistant-1",
                        "parentId": "user-1",
                        "timestamp": "2026-01-01T00:00:02Z",
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "text", "text": "Assistant response"},
                                {"type": "thinking", "thinking": "Private reasoning summary"},
                                {
                                    "type": "toolCall",
                                    "id": "call-1",
                                    "name": "Bash",
                                    "arguments": {"command": "pwd"},
                                },
                                {"type": "redactedThinking", "data": "redacted"},
                            ],
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "message",
                        "id": "tool-result-1",
                        "parentId": "assistant-1",
                        "timestamp": "2026-01-01T00:00:03Z",
                        "message": {
                            "role": "toolResult",
                            "toolCallId": "call-1",
                            "toolName": "Bash",
                            "content": [
                                {"type": "text", "text": "first line"},
                                {"type": "text", "text": "second line"},
                                {"type": "image", "data": "synthetic-base64", "mimeType": "image/png"},
                            ],
                            "isError": False,
                        },
                    }
                ),
                json.dumps({"type": "model_change", "id": "model-1", "timestamp": "2026-01-01T00:00:04Z"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = core.parse_session_file(path)

    assert result["source_format"] == "omp_session"
    assert result["meta"] == {
        "session_id": "omp-session-1",
        "timestamp": "2026-01-01T00:00:00Z",
        "cwd": "/project",
        "title": "Synthetic OMP session",
    }
    loglines = result["loglines"]
    assert len(loglines) == 3
    assert loglines[0] == {
        "type": "user",
        "timestamp": "2026-01-01T00:00:01Z",
        "message": {"role": "user", "content": [{"type": "text", "text": "Prompt for omp-session-1"}]},
    }
    assistant_blocks = loglines[1]["message"]["content"]
    assert assistant_blocks == [
        {"type": "text", "text": "Assistant response"},
        {"type": "thinking", "thinking": "Private reasoning summary"},
        {"type": "tool_use", "name": "Bash", "input": {"command": "pwd"}, "id": "call-1"},
    ]
    assert loglines[2] == {
        "type": "assistant",
        "timestamp": "2026-01-01T00:00:03Z",
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call-1",
                    "content": "first line\nsecond line",
                    "is_error": False,
                }
            ],
        },
    }


def test_parse_session_file_skips_non_dict_leading_lines_for_omp(tmp_path):
    path = tmp_path / "omp-null-lead.jsonl"
    path.write_text(
        "\n".join(
            [
                "null",
                json.dumps(
                    {
                        "type": "title",
                        "v": 1,
                        "title": "Synthetic OMP session",
                        "source": "auto",
                        "updatedAt": "2026-01-01T00:00:00Z",
                        "pad": "",
                    }
                ),
                json.dumps(
                    {
                        "type": "session",
                        "version": 3,
                        "id": "omp-null-lead",
                        "timestamp": "2026-01-01T00:00:00Z",
                        "cwd": "/project",
                        "title": "Synthetic OMP session",
                    }
                ),
                json.dumps(
                    {
                        "type": "message",
                        "id": "user-1",
                        "parentId": None,
                        "timestamp": "2026-01-01T00:00:01Z",
                        "message": {
                            "role": "user",
                            "content": [{"type": "text", "text": "Prompt for omp-null-lead"}],
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = core.parse_session_file(path)

    assert result["source_format"] == "omp_session"
    assert result["loglines"] == [
        {
            "type": "user",
            "timestamp": "2026-01-01T00:00:01Z",
            "message": {"role": "user", "content": [{"type": "text", "text": "Prompt for omp-null-lead"}]},
        }
    ]


def test_build_changelog_digest_tracks_apply_patch_custom_tool_call_input(tmp_path):
    path = tmp_path / "rollout.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-01-01T00:00:00Z",
                        "type": "session_meta",
                        "payload": {"id": "sess-1", "timestamp": "2026-01-01T00:00:00Z"},
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-01-01T00:00:01Z",
                        "type": "response_item",
                        "payload": {
                            "type": "custom_tool_call",
                            "status": "completed",
                            "call_id": "call-apply-patch",
                            "name": "apply_patch",
                            "input": "\n".join(
                                [
                                    "*** Begin Patch",
                                    "*** Update File: foo.txt",
                                    "@@",
                                    "-old",
                                    "+new",
                                    "*** End Patch",
                                ]
                            ),
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    digest = core._build_changelog_digest(
        source_jsonl=path,
        start="2026-01-01T00:00:00+00:00",
        end="2026-01-01T00:00:02+00:00",
    )

    touched = digest["delta"]["touched_files"]
    assert touched["modified"] == ["foo.txt"]


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
