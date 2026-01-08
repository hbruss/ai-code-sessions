"""Tests for CLI commands that were not covered elsewhere."""

import json
from datetime import datetime, timezone
from pathlib import Path

from click.testing import CliRunner

import importlib

cli_module = importlib.import_module("ai_code_sessions.cli")

cli = cli_module.cli


def test_find_source_outputs_path(monkeypatch, tmp_path):
    expected = tmp_path / "session.jsonl"
    expected.write_text("{}", encoding="utf-8")

    def fake_find_best_source_file(**_kwargs):
        return {"best": {"path": str(expected)}}

    monkeypatch.setattr(cli_module, "find_best_source_file", fake_find_best_source_file)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "find-source",
            "--tool",
            "codex",
            "--cwd",
            str(tmp_path),
            "--project-root",
            str(tmp_path),
            "--start",
            "2026-01-01T00:00:00Z",
            "--end",
            "2026-01-01T01:00:00Z",
        ],
    )

    assert result.exit_code == 0
    assert str(expected) in result.output


def test_export_latest_writes_metadata_and_json(monkeypatch, tmp_path):
    source = tmp_path / "source.jsonl"
    source.write_text('{"type":"summary","summary":"ok"}\n', encoding="utf-8")
    output_dir = tmp_path / "out"

    def fake_find_best_source_file(**_kwargs):
        return {"best": {"path": str(source)}}

    def fake_generate_html(_path, out_dir, **_kwargs):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "index.html").write_text("<html></html>", encoding="utf-8")

    monkeypatch.setattr(cli_module, "find_best_source_file", fake_find_best_source_file)
    monkeypatch.setattr(cli_module, "generate_html", fake_generate_html)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "export-latest",
            "--tool",
            "codex",
            "--cwd",
            str(tmp_path),
            "--project-root",
            str(tmp_path),
            "--start",
            "2026-01-01T00:00:00Z",
            "--end",
            "2026-01-01T01:00:00Z",
            "--output",
            str(output_dir),
            "--json",
            "--no-changelog",
        ],
    )

    assert result.exit_code == 0
    assert (output_dir / "index.html").exists()
    assert (output_dir / "source_match.json").exists()
    assert (output_dir / "export_runs.jsonl").exists()
    assert (output_dir / source.name).exists()


def test_resume_latest_invokes_run_ctx_session(monkeypatch, tmp_path):
    session_dir = tmp_path / ".codex" / "sessions" / "2026-01-01-0000_test"
    session_dir.mkdir(parents=True)
    (session_dir / "index.html").write_text("<html>ok</html>", encoding="utf-8")

    def fake_collect_repo_sessions(**_kwargs):
        return [
            {
                "resume_id": "abc123",
                "session_dir": session_dir,
                "label": "Resume me",
            }
        ]

    def fake_resolve_ctx_tool(**_kwargs):
        return tmp_path / ".codex" / "sessions", "codex"

    captured = {}

    def fake_run_ctx_session(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(cli_module, "_collect_repo_sessions", fake_collect_repo_sessions)
    monkeypatch.setattr(cli_module, "_resolve_ctx_tool", fake_resolve_ctx_tool)
    monkeypatch.setattr(cli_module, "_run_ctx_session", fake_run_ctx_session)
    monkeypatch.setattr(cli_module, "_git_toplevel", lambda _cwd: tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["resume", "codex", "--latest", "--no-changelog"])

    assert result.exit_code == 0
    assert captured["tool"] == "codex"
    assert captured["extra_args"] == ["resume", "abc123"]
    assert captured["session_path_override"] == session_dir


def test_archive_generates_index(monkeypatch, tmp_path):
    codex_dir = tmp_path / ".codex" / "sessions" / "2026-01-01-0000_codex"
    claude_dir = tmp_path / ".claude" / "sessions" / "2026-01-02-0000_claude"
    codex_dir.mkdir(parents=True)
    claude_dir.mkdir(parents=True)
    (codex_dir / "index.html").write_text("<html>codex</html>", encoding="utf-8")
    (claude_dir / "index.html").write_text("<html>claude</html>", encoding="utf-8")

    def fake_collect_repo_sessions(base_dir, tool, limit, tz_name):
        if tool == "codex":
            return [
                {
                    "session_dir": codex_dir,
                    "start_dt": datetime(2026, 1, 1, tzinfo=timezone.utc),
                    "label": "Codex run",
                    "duration": "1m",
                    "pages": 1,
                }
            ]
        if tool == "claude":
            return [
                {
                    "session_dir": claude_dir,
                    "start_dt": datetime(2026, 1, 2, tzinfo=timezone.utc),
                    "label": "Claude run",
                    "duration": "2m",
                    "pages": 1,
                }
            ]
        return []

    monkeypatch.setattr(cli_module, "_collect_repo_sessions", fake_collect_repo_sessions)
    monkeypatch.setattr(cli_module, "_git_toplevel", lambda _cwd: tmp_path)

    output_dir = tmp_path / "archive"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["archive", "--project-root", str(tmp_path), "--output", str(output_dir)],
    )

    assert result.exit_code == 0
    index_html = (output_dir / "index.html").read_text(encoding="utf-8")
    assert "Codex run" in index_html
    assert "Claude run" in index_html


def test_config_show_json(monkeypatch, tmp_path):
    def fake_resolve_config_with_provenance(**_kwargs):
        return ({"changelog": {"enabled": True}}, {"changelog.enabled": "default"})

    monkeypatch.setattr(cli_module, "resolve_config_with_provenance", fake_resolve_config_with_provenance)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["config", "show", "--project-root", str(tmp_path), "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["resolved"]["changelog"]["enabled"] is True
    assert payload["provenance"]["changelog.enabled"] == "default"


def test_ctx_invokes_run_ctx_session(monkeypatch, tmp_path):
    captured = {}

    def fake_resolve_ctx_tool(**_kwargs):
        return tmp_path / ".codex" / "sessions", "codex"

    def fake_run_ctx_session(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(cli_module, "_resolve_ctx_tool", fake_resolve_ctx_tool)
    monkeypatch.setattr(cli_module, "_run_ctx_session", fake_run_ctx_session)
    monkeypatch.setattr(cli_module, "_git_toplevel", lambda _cwd: tmp_path)

    runner = CliRunner()
    result = runner.invoke(cli, ["ctx", "--codex", "My Label", "--no-changelog"])

    assert result.exit_code == 0
    assert captured["tool"] == "codex"
    assert captured["label_value"] == "My Label"


def test_ctx_missing_tool_errors():
    runner = CliRunner()
    result = runner.invoke(cli, ["ctx"])

    assert result.exit_code != 0
    assert "Missing or invalid --tool" in result.output


def test_main_invokes_cli(monkeypatch):
    import ai_code_sessions

    called = {}

    def fake_cli():
        called["ran"] = True

    monkeypatch.setattr(ai_code_sessions, "cli", fake_cli)
    ai_code_sessions.main()

    assert called["ran"] is True
