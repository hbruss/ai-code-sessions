"""Tests for changelog backfill concurrency/progress paths."""

import io
import json

from click.testing import CliRunner

import importlib

cli_module = importlib.import_module("ai_code_sessions.cli")
cli = cli_module.cli


class _TTYStringIO(io.StringIO):
    def isatty(self):
        return True


def test_changelog_backfill_concurrency_progress(monkeypatch, tmp_path):
    root = tmp_path
    session_dir = root / ".claude" / "sessions" / "2026-01-01-0000_test"
    session_dir.mkdir(parents=True)

    copied_jsonl = session_dir / "session.jsonl"
    copied_jsonl.write_text("{}", encoding="utf-8")

    source_match = session_dir / "source_match.json"
    source_match.write_text("{}", encoding="utf-8")

    export_runs = session_dir / "export_runs.jsonl"
    export_runs.write_text(
        json.dumps(
            {
                "start": "2026-01-01T00:00:00+00:00",
                "end": "2026-01-01T01:00:00+00:00",
                "tool": "claude",
                "cwd": str(root),
                "project_root": str(root),
                "copied_jsonl": str(copied_jsonl),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    def fake_append(**_kwargs):
        return True, "run-123", "appended"

    monkeypatch.setattr(cli_module, "_generate_and_append_changelog_entry", fake_append)
    monkeypatch.setattr(cli_module.sys, "stderr", _TTYStringIO())
    monkeypatch.setenv("CTX_CHANGELOG_PROGRESS", "1")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "changelog",
            "backfill",
            "--project-root",
            str(root),
            "--evaluator",
            "claude",
            "--max-concurrency",
            "2",
        ],
    )

    assert result.exit_code == 0
    assert "Backfill complete" in result.output
