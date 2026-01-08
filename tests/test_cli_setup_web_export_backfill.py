"""Tests for CLI setup/web/backfill/export-latest flows."""

import json

import click
from click.testing import CliRunner

import importlib

cli_module = importlib.import_module("ai_code_sessions.cli")
cli = cli_module.cli


class _Answer:
    def __init__(self, value):
        self._value = value

    def ask(self):
        return self._value


def test_setup_writes_configs_and_gitignore(monkeypatch, tmp_path):
    answers = iter(
        [
            "alice",  # actor
            "America/Los_Angeles",  # tz
            True,  # changelog enabled
            "codex",  # evaluator
            "",  # model
            False,  # commit changelog
        ]
    )

    monkeypatch.setattr(cli_module.questionary, "text", lambda *a, **k: _Answer(next(answers)))
    monkeypatch.setattr(cli_module.questionary, "confirm", lambda *a, **k: _Answer(next(answers)))
    monkeypatch.setattr(cli_module.questionary, "select", lambda *a, **k: _Answer(next(answers)))

    global_path = tmp_path / "global.toml"
    monkeypatch.setattr(cli_module, "_global_config_path", lambda: global_path)
    monkeypatch.setattr(cli_module, "_render_config_toml", lambda _cfg: "cfg")
    captured = {}

    def fake_ensure_gitignore(root, entry):
        captured["root"] = root
        captured["entry"] = entry

    monkeypatch.setattr(cli_module, "_ensure_gitignore_ignores", fake_ensure_gitignore)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "setup",
            "--project-root",
            str(tmp_path),
            "--global",
            "--repo",
            "--force",
        ],
    )

    assert result.exit_code == 0
    assert global_path.exists()
    assert (tmp_path / ".ai-code-sessions.toml").exists()
    assert captured["entry"] == ".changelog/"


def test_web_happy_path_writes_json(monkeypatch, tmp_path):
    session_data = {
        "loglines": [
            {
                "type": "user",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {"role": "user", "content": "Hello"},
            }
        ]
    }

    monkeypatch.setattr(cli_module, "resolve_credentials", lambda *_: ("tok", "org"))
    monkeypatch.setattr(cli_module, "fetch_session", lambda *_: session_data)

    def fake_generate(session, output, **_kwargs):
        output.mkdir(parents=True, exist_ok=True)
        (output / "index.html").write_text("<html></html>", encoding="utf-8")

    monkeypatch.setattr(cli_module, "generate_html_from_session_data", fake_generate)

    output_dir = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "web",
            "sess-1",
            "--token",
            "tok",
            "--org-uuid",
            "org",
            "--output",
            str(output_dir),
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert (output_dir / "index.html").exists()
    assert (output_dir / "sess-1.json").exists()


def test_backfill_sequential_appends(monkeypatch, tmp_path):
    session_dir = tmp_path / ".codex" / "sessions" / "2026-01-01-0000_test"
    session_dir.mkdir(parents=True)

    copied_jsonl = session_dir / "rollout-abc.jsonl"
    copied_jsonl.write_text("{}", encoding="utf-8")

    export_runs = session_dir / "export_runs.jsonl"
    export_runs.write_text(
        json.dumps(
            {
                "start": "2026-01-01T00:00:00+00:00",
                "end": "2026-01-01T01:00:00+00:00",
                "tool": "codex",
                "copied_jsonl": str(copied_jsonl),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    def fake_append(**_kwargs):
        return True, "run-123", "appended"

    monkeypatch.setattr(cli_module, "_generate_and_append_changelog_entry", fake_append)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "changelog",
            "backfill",
            "--project-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "Backfill: appended" in result.output


def test_export_latest_find_best_source_error(monkeypatch, tmp_path):
    def fake_find_best_source_file(**_kwargs):
        raise click.ClickException("boom")

    monkeypatch.setattr(cli_module, "find_best_source_file", fake_find_best_source_file)

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
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code != 0
    assert "boom" in result.output
