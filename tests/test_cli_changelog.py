"""Tests for changelog CLI commands."""

import json
from datetime import timedelta
from pathlib import Path

from click.testing import CliRunner

import importlib
import ai_code_sessions as core_module

cli_module = importlib.import_module("ai_code_sessions.cli")

cli = cli_module.cli


class _Answer:
    def __init__(self, value):
        self._value = value

    def ask(self):
        return self._value


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


def test_changelog_refresh_metadata_supports_sync_generated_entry_shape(tmp_path):
    actor_dir = tmp_path / ".changelog" / "alice"
    actor_dir.mkdir(parents=True)
    entries_path = actor_dir / "entries.jsonl"

    source_jsonl = tmp_path / "rollout.jsonl"
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

    start = "2026-01-01T00:00:00+00:00"
    end = "2026-01-01T00:00:02+00:00"
    entry = {
        "run_id": "run-sync-1",
        "created_at": start,
        "tool": "codex",
        "actor": "alice",
        "project": "demo",
        "project_root": str(tmp_path),
        "label": "Test",
        "start": start,
        "end": end,
        "session_dir": str(source_jsonl.parent),
        "continuation_of_run_id": None,
        "transcript": {
            "output_dir": None,
            "index_html": None,
            "source_jsonl": str(source_jsonl.resolve()),
            "source_match_json": None,
        },
        "source": {
            "kind": "native_session",
            "identity": core_module._canonical_session_identity_for_source(
                tool="codex",
                source_jsonl=source_jsonl,
                start=start,
                end=end,
            ),
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
    refreshed = json.loads(entries_path.read_text(encoding="utf-8").strip())
    assert refreshed["touched_files"]["modified"] == ["foo.txt"]


def test_changelog_sync_defaults_to_48_hours(monkeypatch, tmp_path):
    captured = {}

    def fake_discover_native_sessions(*, tools, since, until):
        captured["tools"] = tools
        captured["since"] = since
        captured["until"] = until
        return []

    monkeypatch.setattr(cli_module, "_discover_native_sessions", fake_discover_native_sessions)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "changelog",
            "sync",
            "--project-root",
            str(tmp_path),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert captured["tools"] == ("codex", "claude")
    assert timedelta(hours=47, minutes=59) <= captured["until"] - captured["since"] <= timedelta(hours=48, minutes=1)
    assert "[DRY RUN]" in result.output
    assert "processed=0" in result.output


def test_changelog_sync_uses_env_evaluator_default_when_flag_omitted(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    source_jsonl = tmp_path / "rollout-env-default.jsonl"
    source_jsonl.write_text("{}", encoding="utf-8")

    monkeypatch.setenv("CTX_CHANGELOG_EVALUATOR", "claude")
    monkeypatch.setattr(
        cli_module,
        "_discover_native_sessions",
        lambda **_kwargs: [
            {
                "tool": "codex",
                "source_jsonl": str(source_jsonl),
                "start": "2026-01-01T00:00:00+00:00",
                "end": "2026-01-01T00:05:00+00:00",
                "session_id": "codex-env-default",
                "prompt_summary": "Use env evaluator default",
            }
        ],
    )
    monkeypatch.setattr(
        cli_module,
        "_resolve_native_session_project",
        lambda _candidate: {
            "project_root": str(repo_root),
            "confidence": "high",
            "reason": "cwd resolves to a git toplevel with consistent evidence",
            "evidence": {"plausible_project_roots": [str(repo_root)]},
        },
    )
    captured = {}

    def fake_generate_and_append(**kwargs):
        captured.update(kwargs)
        return True, "run-env-default", "appended"

    monkeypatch.setattr(cli_module, "_generate_and_append_changelog_entry", fake_generate_and_append)

    runner = CliRunner()
    result = runner.invoke(cli, ["changelog", "sync", "--codex"])

    assert result.exit_code == 0
    assert captured["evaluator"] == "claude"
    assert "processed=1" in result.output


def test_changelog_sync_uses_config_evaluator_default_when_flag_omitted(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    source_jsonl = tmp_path / "rollout-config-default.jsonl"
    source_jsonl.write_text("{}", encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text('[changelog]\nevaluator = "claude"\n', encoding="utf-8")

    monkeypatch.setenv("AI_CODE_SESSIONS_CONFIG", str(config_path))
    monkeypatch.delenv("CTX_CHANGELOG_EVALUATOR", raising=False)
    monkeypatch.delenv("AI_CODE_SESSIONS_CHANGELOG_EVALUATOR", raising=False)
    monkeypatch.setattr(
        cli_module,
        "_discover_native_sessions",
        lambda **_kwargs: [
            {
                "tool": "codex",
                "source_jsonl": str(source_jsonl),
                "start": "2026-01-01T00:00:00+00:00",
                "end": "2026-01-01T00:05:00+00:00",
                "session_id": "codex-config-default",
                "prompt_summary": "Use config evaluator default",
            }
        ],
    )
    monkeypatch.setattr(
        cli_module,
        "_resolve_native_session_project",
        lambda _candidate: {
            "project_root": str(repo_root),
            "confidence": "high",
            "reason": "cwd resolves to a git toplevel with consistent evidence",
            "evidence": {"plausible_project_roots": [str(repo_root)]},
        },
    )
    captured = {}

    def fake_generate_and_append(**kwargs):
        captured.update(kwargs)
        return True, "run-config-default", "appended"

    monkeypatch.setattr(cli_module, "_generate_and_append_changelog_entry", fake_generate_and_append)

    runner = CliRunner()
    result = runner.invoke(cli, ["changelog", "sync", "--codex", "--project-root", str(repo_root)])

    assert result.exit_code == 0
    assert captured["evaluator"] == "claude"
    assert "processed=1" in result.output


def test_changelog_sync_uses_each_resolved_repo_config_when_project_root_is_omitted(monkeypatch, tmp_path):
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    (repo_a / ".ai-code-sessions.toml").write_text('[changelog]\nevaluator = "claude"\n', encoding="utf-8")
    (repo_b / ".ai-code-sessions.toml").write_text('[changelog]\nevaluator = "codex"\n', encoding="utf-8")
    source_a = tmp_path / "rollout-repo-a.jsonl"
    source_b = tmp_path / "rollout-repo-b.jsonl"
    source_a.write_text("{}", encoding="utf-8")
    source_b.write_text("{}", encoding="utf-8")

    monkeypatch.delenv("AI_CODE_SESSIONS_CONFIG", raising=False)
    monkeypatch.delenv("CTX_CHANGELOG_EVALUATOR", raising=False)
    monkeypatch.delenv("AI_CODE_SESSIONS_CHANGELOG_EVALUATOR", raising=False)
    monkeypatch.setattr(
        cli_module,
        "_discover_native_sessions",
        lambda **_kwargs: [
            {
                "tool": "codex",
                "source_jsonl": str(source_a),
                "start": "2026-01-01T00:00:00+00:00",
                "end": "2026-01-01T00:05:00+00:00",
                "session_id": "codex-repo-a",
                "prompt_summary": "Use repo A config",
            },
            {
                "tool": "codex",
                "source_jsonl": str(source_b),
                "start": "2026-01-01T01:00:00+00:00",
                "end": "2026-01-01T01:05:00+00:00",
                "session_id": "codex-repo-b",
                "prompt_summary": "Use repo B config",
            },
        ],
    )
    monkeypatch.setattr(
        cli_module,
        "_resolve_native_session_project",
        lambda candidate: {
            "project_root": str(repo_a if Path(candidate["source_jsonl"]) == source_a else repo_b),
            "confidence": "high",
            "reason": "cwd resolves to a git toplevel with consistent evidence",
            "evidence": {
                "plausible_project_roots": [str(repo_a if Path(candidate["source_jsonl"]) == source_a else repo_b)]
            },
        },
    )
    captured = []

    def fake_generate_and_append(**kwargs):
        captured.append({"project_root": str(kwargs["project_root"]), "evaluator": kwargs["evaluator"]})
        return True, f"run-{len(captured)}", "appended"

    monkeypatch.setattr(cli_module, "_generate_and_append_changelog_entry", fake_generate_and_append)

    runner = CliRunner()
    result = runner.invoke(cli, ["changelog", "sync", "--codex"])

    assert result.exit_code == 0
    assert captured == [
        {"project_root": str(repo_a), "evaluator": "claude"},
        {"project_root": str(repo_b), "evaluator": "codex"},
    ]
    assert "processed=2" in result.output


def test_changelog_sync_explicit_evaluator_overrides_env_and_config(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    source_jsonl = tmp_path / "rollout-explicit-evaluator.jsonl"
    source_jsonl.write_text("{}", encoding="utf-8")
    config_path = tmp_path / "config.toml"
    config_path.write_text('[changelog]\nevaluator = "claude"\n', encoding="utf-8")

    monkeypatch.setenv("AI_CODE_SESSIONS_CONFIG", str(config_path))
    monkeypatch.setenv("CTX_CHANGELOG_EVALUATOR", "claude")
    monkeypatch.setattr(
        cli_module,
        "_discover_native_sessions",
        lambda **_kwargs: [
            {
                "tool": "codex",
                "source_jsonl": str(source_jsonl),
                "start": "2026-01-01T00:00:00+00:00",
                "end": "2026-01-01T00:05:00+00:00",
                "session_id": "codex-explicit-evaluator",
                "prompt_summary": "Use explicit evaluator",
            }
        ],
    )
    monkeypatch.setattr(
        cli_module,
        "_resolve_native_session_project",
        lambda _candidate: {
            "project_root": str(repo_root),
            "confidence": "high",
            "reason": "cwd resolves to a git toplevel with consistent evidence",
            "evidence": {"plausible_project_roots": [str(repo_root)]},
        },
    )
    captured = {}

    def fake_generate_and_append(**kwargs):
        captured.update(kwargs)
        return True, "run-explicit-evaluator", "appended"

    monkeypatch.setattr(cli_module, "_generate_and_append_changelog_entry", fake_generate_and_append)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["changelog", "sync", "--codex", "--project-root", str(repo_root), "--evaluator", "codex"],
    )

    assert result.exit_code == 0
    assert captured["evaluator"] == "codex"
    assert "processed=1" in result.output


def test_changelog_sync_prompts_for_medium_confidence(monkeypatch, tmp_path):
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    source_jsonl = tmp_path / "rollout-medium.jsonl"
    source_jsonl.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        cli_module,
        "_discover_native_sessions",
        lambda **_kwargs: [
            {
                "tool": "codex",
                "source_jsonl": str(source_jsonl),
                "start": "2026-01-01T00:00:00+00:00",
                "end": "2026-01-01T00:05:00+00:00",
                "session_id": "codex-medium",
                "prompt_summary": "Investigate changelog sync",
            }
        ],
    )
    monkeypatch.setattr(
        cli_module,
        "_resolve_native_session_project",
        lambda _candidate: {
            "project_root": None,
            "confidence": "medium",
            "reason": "Multiple plausible repos found; user selection is required",
            "evidence": {
                "plausible_project_roots": [str(repo_a), str(repo_b)],
                "prompt_summary": "Investigate changelog sync",
            },
        },
    )
    prompts = []
    monkeypatch.setattr(cli_module, "_can_prompt_for_changelog_sync_root", lambda: True)

    def fake_select(message, choices):
        prompts.append(
            {
                "message": message,
                "choices": [getattr(choice, "value", choice) for choice in choices],
            }
        )
        return _Answer(str(repo_b))

    monkeypatch.setattr(cli_module.questionary, "select", fake_select)
    captured = {}

    def fake_generate_and_append(**kwargs):
        captured.update(kwargs)
        return True, "run-sync-1", "appended"

    monkeypatch.setattr(cli_module, "_generate_and_append_changelog_entry", fake_generate_and_append)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "changelog",
            "sync",
            "--codex",
            "--actor",
            "alice",
        ],
    )

    assert result.exit_code == 0
    assert prompts
    assert captured["project_root"] == repo_b.resolve()
    assert captured["session_dir"] == source_jsonl.parent.resolve()
    assert captured["source_jsonl"] == source_jsonl.resolve()
    assert captured["source_match_json"] is None
    assert captured["transcript_output_dir"] is None
    assert captured["transcript_index_html"] is None
    assert "processed=1" in result.output


def test_changelog_sync_skips_low_confidence_sessions(monkeypatch, tmp_path):
    source_jsonl = tmp_path / "claude-low.jsonl"
    source_jsonl.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        cli_module,
        "_discover_native_sessions",
        lambda **_kwargs: [
            {
                "tool": "claude",
                "source_jsonl": str(source_jsonl),
                "start": "2026-01-01T00:00:00+00:00",
                "end": "2026-01-01T00:10:00+00:00",
                "session_id": "claude-low",
                "prompt_summary": "Unclear repo ownership",
            }
        ],
    )
    monkeypatch.setattr(
        cli_module,
        "_resolve_native_session_project",
        lambda _candidate: {
            "project_root": None,
            "confidence": "low",
            "reason": "No trustworthy repo evidence found",
            "evidence": {
                "plausible_project_roots": [],
                "prompt_summary": "Unclear repo ownership",
            },
        },
    )

    def fail_if_called(**_kwargs):
        raise AssertionError("append should not be called for unresolved candidates")

    monkeypatch.setattr(cli_module, "_generate_and_append_changelog_entry", fail_if_called)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "changelog",
            "sync",
            "--claude",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "unresolved" in result.output.lower()
    assert "[DRY RUN]" in result.output
    assert "processed=0" in result.output
    assert "unresolved=1" in result.output


def test_changelog_sync_project_root_suppresses_prompt_for_medium_confidence(monkeypatch, tmp_path):
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    source_jsonl = tmp_path / "rollout-medium-explicit.jsonl"
    source_jsonl.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        cli_module,
        "_discover_native_sessions",
        lambda **_kwargs: [
            {
                "tool": "codex",
                "source_jsonl": str(source_jsonl),
                "start": "2026-01-01T00:00:00+00:00",
                "end": "2026-01-01T00:05:00+00:00",
                "session_id": "codex-medium-explicit",
                "prompt_summary": "Apply explicit project root first",
            }
        ],
    )
    monkeypatch.setattr(
        cli_module,
        "_resolve_native_session_project",
        lambda _candidate: {
            "project_root": None,
            "confidence": "medium",
            "reason": "Multiple plausible repos found; user selection is required",
            "evidence": {
                "plausible_project_roots": [str(repo_a), str(repo_b)],
                "prompt_summary": "Apply explicit project root first",
            },
        },
    )

    def fail_if_prompted(*_args, **_kwargs):
        raise AssertionError("questionary.select() should not be called when --project-root resolves the choice")

    monkeypatch.setattr(cli_module.questionary, "select", fail_if_prompted)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "changelog",
            "sync",
            "--codex",
            "--project-root",
            str(repo_b),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "[DRY RUN] Sync: would append run_id=" in result.output
    assert "processed=1" in result.output
    assert "skipped=0" in result.output


def test_changelog_sync_dry_run_reports_existing_candidates(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    source_jsonl = tmp_path / "rollout-existing.jsonl"
    source_jsonl.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        cli_module,
        "_discover_native_sessions",
        lambda **_kwargs: [
            {
                "tool": "codex",
                "source_jsonl": str(source_jsonl),
                "start": "2026-01-01T00:00:00+00:00",
                "end": "2026-01-01T00:05:00+00:00",
                "session_id": "codex-existing",
                "prompt_summary": "Already changelogged",
            }
        ],
    )
    monkeypatch.setattr(
        cli_module,
        "_resolve_native_session_project",
        lambda _candidate: {
            "project_root": str(repo_root),
            "confidence": "high",
            "reason": "cwd resolves to a git toplevel with consistent evidence",
            "evidence": {
                "plausible_project_roots": [str(repo_root)],
            },
        },
    )
    monkeypatch.setattr(
        cli_module,
        "_preview_changelog_append_status",
        lambda **_kwargs: ("run-existing", "exists"),
    )

    def fail_if_called(**_kwargs):
        raise AssertionError("append should not run during dry-run")

    monkeypatch.setattr(cli_module, "_generate_and_append_changelog_entry", fail_if_called)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "changelog",
            "sync",
            "--codex",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "[DRY RUN] Sync: would skip existing run_id=run-existing" in result.output
    assert "processed=0" in result.output
    assert "skipped=1" in result.output


def test_changelog_sync_medium_confidence_prefilled_root_still_prompts(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    source_jsonl = tmp_path / "rollout-medium-prefilled.jsonl"
    source_jsonl.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        cli_module,
        "_discover_native_sessions",
        lambda **_kwargs: [
            {
                "tool": "codex",
                "source_jsonl": str(source_jsonl),
                "start": "2026-01-01T00:00:00+00:00",
                "end": "2026-01-01T00:05:00+00:00",
                "session_id": "codex-medium-prefilled",
                "prompt_summary": "Prefilled medium root",
            }
        ],
    )
    monkeypatch.setattr(
        cli_module,
        "_resolve_native_session_project",
        lambda _candidate: {
            "project_root": str(repo_root),
            "confidence": "medium",
            "reason": "Repo evidence is plausible but not strong enough for automatic writes",
            "evidence": {
                "plausible_project_roots": [str(repo_root)],
            },
        },
    )
    monkeypatch.setattr(cli_module, "_can_prompt_for_changelog_sync_root", lambda: True)
    prompts = []

    def fake_select(message, choices):
        prompts.append((message, [getattr(choice, "value", choice) for choice in choices]))
        return _Answer(str(repo_root))

    monkeypatch.setattr(cli_module.questionary, "select", fake_select)
    monkeypatch.setattr(
        cli_module,
        "_generate_and_append_changelog_entry",
        lambda **_kwargs: (True, "run-medium-prefilled", "appended"),
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["changelog", "sync", "--codex"])

    assert result.exit_code == 0
    assert prompts
    assert "processed=1" in result.output


def test_changelog_sync_medium_confidence_without_tty_is_unresolved(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    source_jsonl = tmp_path / "rollout-medium-notty.jsonl"
    source_jsonl.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        cli_module,
        "_discover_native_sessions",
        lambda **_kwargs: [
            {
                "tool": "codex",
                "source_jsonl": str(source_jsonl),
                "start": "2026-01-01T00:00:00+00:00",
                "end": "2026-01-01T00:05:00+00:00",
                "session_id": "codex-medium-notty",
                "prompt_summary": "Needs operator choice",
            }
        ],
    )
    monkeypatch.setattr(
        cli_module,
        "_resolve_native_session_project",
        lambda _candidate: {
            "project_root": None,
            "confidence": "medium",
            "reason": "Multiple plausible repos found; user selection is required",
            "evidence": {
                "plausible_project_roots": [str(repo_root)],
            },
        },
    )
    monkeypatch.setattr(cli_module, "_can_prompt_for_changelog_sync_root", lambda: False)

    def fail_if_prompted(*_args, **_kwargs):
        raise AssertionError("questionary.select() should not be called without a TTY")

    monkeypatch.setattr(cli_module.questionary, "select", fail_if_prompted)

    runner = CliRunner()
    result = runner.invoke(cli, ["changelog", "sync", "--codex"])

    assert result.exit_code == 0
    assert "interactive repo selection required" in result.output
    assert "unresolved=1" in result.output


def test_changelog_sync_project_root_limit_applies_after_repo_filter(monkeypatch, tmp_path):
    repo_target = tmp_path / "repo-target"
    repo_other = tmp_path / "repo-other"
    repo_target.mkdir()
    repo_other.mkdir()
    other_source = tmp_path / "rollout-other.jsonl"
    target_one = tmp_path / "rollout-target-one.jsonl"
    target_two = tmp_path / "rollout-target-two.jsonl"
    for path in (other_source, target_one, target_two):
        path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        cli_module,
        "_discover_native_sessions",
        lambda **_kwargs: [
            {
                "tool": "codex",
                "source_jsonl": str(other_source),
                "start": "2026-01-01T00:00:00+00:00",
                "end": "2026-01-01T00:05:00+00:00",
                "session_id": "other",
                "prompt_summary": "Other repo",
            },
            {
                "tool": "codex",
                "source_jsonl": str(target_one),
                "start": "2026-01-01T00:10:00+00:00",
                "end": "2026-01-01T00:15:00+00:00",
                "session_id": "target-one",
                "prompt_summary": "Target one",
            },
            {
                "tool": "codex",
                "source_jsonl": str(target_two),
                "start": "2026-01-01T00:20:00+00:00",
                "end": "2026-01-01T00:25:00+00:00",
                "session_id": "target-two",
                "prompt_summary": "Target two",
            },
        ],
    )

    def fake_resolve(candidate):
        source_name = Path(str(candidate["source_jsonl"])).name
        repo_root = repo_other if source_name == other_source.name else repo_target
        return {
            "project_root": str(repo_root),
            "confidence": "high",
            "reason": "cwd resolves to a git toplevel with consistent evidence",
            "evidence": {
                "plausible_project_roots": [str(repo_root)],
            },
        }

    monkeypatch.setattr(cli_module, "_resolve_native_session_project", fake_resolve)
    monkeypatch.setattr(
        cli_module,
        "_preview_changelog_append_status",
        lambda **_kwargs: ("run-preview", "appended"),
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "changelog",
            "sync",
            "--codex",
            "--project-root",
            str(repo_target),
            "--limit",
            "1",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "rollout-other.jsonl" in result.output
    assert str(repo_other.resolve()) in result.output
    assert "rollout-target-one.jsonl" in result.output
    assert "rollout-target-two.jsonl" not in result.output
    assert "processed=1" in result.output
