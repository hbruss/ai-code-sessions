"""Tests for changelog CLI commands."""

import json
from pathlib import Path

from click.testing import CliRunner

import importlib

cli_module = importlib.import_module("ai_code_sessions.cli")

cli = cli_module.cli


def _write_entries(path: Path, entries: list[str]) -> None:
    path.write_text("\n".join(entries) + "\n", encoding="utf-8")


def test_changelog_since_filters_by_date_and_tag(tmp_path):
    actor_dir = tmp_path / ".changelog" / "alice"
    actor_dir.mkdir(parents=True)
    entries_path = actor_dir / "entries.jsonl"

    entry_new = {
        "run_id": "run-new",
        "created_at": "2026-01-02T12:00:00+00:00",
        "tool": "codex",
        "actor": "alice",
        "summary": "New entry",
        "bullets": ["Did the new thing."],
        "tags": ["feat"],
        "label": "New work",
        "start": "2026-01-02T12:00:00+00:00",
    }
    entry_old = {
        "run_id": "run-old",
        "created_at": "2025-12-31T12:00:00+00:00",
        "tool": "codex",
        "actor": "alice",
        "summary": "Old entry",
        "bullets": ["Did the old thing."],
        "tags": ["fix"],
        "label": "Old work",
        "start": "2025-12-31T12:00:00+00:00",
    }
    _write_entries(entries_path, [json.dumps(entry_new), json.dumps(entry_old)])

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "changelog",
            "since",
            "2026-01-01",
            "--project-root",
            str(tmp_path),
            "--format",
            "json",
            "--tag",
            "feat",
        ],
    )

    assert result.exit_code == 0
    entries = json.loads(result.output)
    assert len(entries) == 1
    assert entries[0]["run_id"] == "run-new"


def test_changelog_lint_fix_preserves_invalid_lines(monkeypatch, tmp_path):
    actor_dir = tmp_path / ".changelog" / "bob"
    actor_dir.mkdir(parents=True)
    entries_path = actor_dir / "entries.jsonl"

    source_jsonl = tmp_path / "source.jsonl"
    source_jsonl.write_text("{}", encoding="utf-8")

    valid_entry = {
        "summary": "Good summary.",
        "bullets": ["All good."],
        "tags": [],
    }
    bad_entry = {
        "summary": "Needs fix",
        "bullets": ["Truncat"],
        "tags": ["bug"],
        "project_root": str(tmp_path),
        "start": "2026-01-01T00:00:00+00:00",
        "end": "2026-01-01T01:00:00+00:00",
        "transcript": {
            "source_jsonl": str(source_jsonl),
        },
    }

    _write_entries(
        entries_path,
        [
            json.dumps(valid_entry),
            "{not json}",
            json.dumps(bad_entry),
        ],
    )

    def fake_build_changelog_digest(**_kwargs):
        return {
            "delta": {
                "touched_files": {"created": [], "modified": [], "deleted": [], "moved": []},
                "tests": [],
                "commits": [],
            }
        }

    def fake_run_eval(**_kwargs):
        return {
            "summary": "Fixed summary",
            "bullets": ["Fixed bullet."],
            "tags": ["fix"],
            "notes": "Note ✅",
        }

    monkeypatch.setattr(cli_module, "_build_changelog_digest", fake_build_changelog_digest)
    monkeypatch.setattr(cli_module, "_run_codex_changelog_evaluator", fake_run_eval)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "changelog",
            "lint",
            "--project-root",
            str(tmp_path),
            "--fix",
            "--evaluator",
            "codex",
        ],
    )

    assert result.exit_code == 0
    assert entries_path.with_suffix(".jsonl.bak").exists()

    lines = entries_path.read_text(encoding="utf-8").splitlines()
    assert lines[1] == "{not json}"
    fixed = json.loads(lines[2])
    assert fixed["summary"] == "Fixed summary"


def test_changelog_refresh_metadata_updates_touched_files_without_evaluator(tmp_path):
    actor_dir = tmp_path / ".changelog" / "alice"
    actor_dir.mkdir(parents=True)
    entries_path = actor_dir / "entries.jsonl"

    source_jsonl = tmp_path / "rollout.jsonl"
    source_match_json = tmp_path / "source_match.json"
    source_match_json.write_text("{}", encoding="utf-8")

    source_jsonl.write_text(
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

    entry = {
        "run_id": "run-1",
        "created_at": "2026-01-01T00:00:00+00:00",
        "tool": "codex",
        "actor": "alice",
        "project": "demo",
        "project_root": str(tmp_path),
        "label": "Test",
        "start": "2026-01-01T00:00:00+00:00",
        "end": "2026-01-01T00:00:02+00:00",
        "session_dir": str(tmp_path / ".codex" / "sessions" / "2026-01-01-0000_Test"),
        "continuation_of_run_id": None,
        "transcript": {
            "output_dir": str(tmp_path),
            "index_html": str(tmp_path / "index.html"),
            "source_jsonl": str(source_jsonl),
            "source_match_json": str(source_match_json),
        },
        "summary": "test",
        "bullets": ["did thing"],
        "tags": [],
        "touched_files": {"created": [], "modified": [], "deleted": [], "moved": []},
        "tests": [],
        "commits": [],
        "notes": None,
    }
    entries_path.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "changelog",
            "refresh-metadata",
            "--project-root",
            str(tmp_path),
            "--actor",
            "alice",
        ],
    )

    assert result.exit_code == 0
    assert entries_path.with_suffix(".jsonl.bak").exists()
    refreshed = json.loads(entries_path.read_text(encoding="utf-8").strip())
    assert refreshed["touched_files"]["modified"] == ["foo.txt"]
