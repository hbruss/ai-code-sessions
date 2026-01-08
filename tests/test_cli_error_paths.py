"""Tests for CLI error paths and guardrails."""

import httpx
from click.testing import CliRunner

import importlib

cli_module = importlib.import_module("ai_code_sessions.cli")
cli = cli_module.cli


def test_changelog_backfill_invalid_tokens_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CTX_CHANGELOG_CLAUDE_THINKING_TOKENS", "nope")
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "changelog",
            "backfill",
            "--project-root",
            str(tmp_path),
            "--evaluator",
            "claude",
        ],
    )

    assert result.exit_code != 0
    assert "CTX_CHANGELOG_CLAUDE_THINKING_TOKENS" in result.output


def test_changelog_backfill_invalid_max_concurrency(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "changelog",
            "backfill",
            "--project-root",
            str(tmp_path),
            "--max-concurrency",
            "0",
        ],
    )

    assert result.exit_code != 0
    assert "max-concurrency" in result.output


def test_changelog_backfill_limit_requires_single_worker(tmp_path):
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "changelog",
            "backfill",
            "--project-root",
            str(tmp_path),
            "--evaluator",
            "claude",
            "--max-concurrency",
            "2",
            "--limit",
            "1",
        ],
    )

    assert result.exit_code != 0
    assert "--limit is only supported" in result.output


def test_changelog_backfill_skips_missing_jsonl(tmp_path):
    session_dir = tmp_path / ".codex" / "sessions" / "2026-01-01-0000_test"
    session_dir.mkdir(parents=True)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "changelog",
            "backfill",
            "--project-root",
            str(tmp_path),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "Backfill: skipping" in result.output


def test_web_no_sessions(monkeypatch):
    monkeypatch.setattr(cli_module, "resolve_credentials", lambda *_: ("tok", "org"))
    monkeypatch.setattr(cli_module, "fetch_sessions", lambda *_: [])

    runner = CliRunner()
    result = runner.invoke(cli, ["web"])

    assert result.exit_code == 0
    assert "No sessions found." in result.output


def test_web_selection_cancelled(monkeypatch):
    monkeypatch.setattr(cli_module, "resolve_credentials", lambda *_: ("tok", "org"))
    monkeypatch.setattr(cli_module, "fetch_sessions", lambda *_: [{"id": "sess-1"}])

    class MockSelect:
        def __init__(self, *args, **kwargs):
            pass

        def ask(self):
            return None

    monkeypatch.setattr(cli_module.questionary, "select", MockSelect)

    runner = CliRunner()
    result = runner.invoke(cli, ["web"])

    assert result.exit_code == 0
    assert "No session selected." in result.output


def test_web_fetch_sessions_request_error(monkeypatch):
    monkeypatch.setattr(cli_module, "resolve_credentials", lambda *_: ("tok", "org"))

    def fake_fetch_sessions(*_args, **_kwargs):
        raise httpx.RequestError("boom", request=httpx.Request("GET", "https://example.com"))

    monkeypatch.setattr(cli_module, "fetch_sessions", fake_fetch_sessions)

    runner = CliRunner()
    result = runner.invoke(cli, ["web"])

    assert result.exit_code != 0
    assert "Request failed" in result.output
