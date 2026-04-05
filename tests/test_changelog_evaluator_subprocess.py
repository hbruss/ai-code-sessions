"""Tests for evaluator subprocess parsing and error handling."""

import json
from subprocess import CompletedProcess
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

import ai_code_sessions.core as core


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_codex_evaluator_error_includes_stdout_stderr(monkeypatch, tmp_path):
    def fake_which(_name):
        return "/usr/bin/codex"

    def fake_run(*_args, **_kwargs):
        return _Proc(returncode=1, stdout="bad stdout", stderr="bad stderr")

    monkeypatch.setattr(core.shutil, "which", fake_which)
    monkeypatch.setattr(core.subprocess, "run", fake_run)

    schema_path = tmp_path / "schema.json"
    schema_path.write_text("{}", encoding="utf-8")

    with CliRunner().isolated_filesystem():
        try:
            core._run_codex_changelog_evaluator(
                prompt="hi",
                schema_path=schema_path,
                cd=tmp_path,
                model="gpt",
            )
        except click.ClickException as exc:
            assert "codex exec failed" in str(exc)
            assert "stderr_tail" in str(exc)
            assert "stdout_tail" in str(exc)
        else:
            raise AssertionError("Expected ClickException")


def test_codex_evaluator_markdown_json_falls_back_to_error(monkeypatch, tmp_path):
    def fake_which(_name):
        return "/usr/bin/codex"

    def fake_run(cmd, **_kwargs):
        out_path = Path(cmd[cmd.index("--output-last-message") + 1])
        # This matches the current salvage regex which expects escaped braces.
        out_path.write_text('\\{"summary": "ok"\\}', encoding="utf-8")
        return _Proc(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(core.shutil, "which", fake_which)
    monkeypatch.setattr(core.subprocess, "run", fake_run)

    schema_path = tmp_path / "schema.json"
    schema_path.write_text("{}", encoding="utf-8")

    try:
        core._run_codex_changelog_evaluator(
            prompt="hi",
            schema_path=schema_path,
            cd=tmp_path,
            model="gpt",
        )
    except click.ClickException as exc:
        assert "codex output was not valid JSON" in str(exc)
    else:
        raise AssertionError("Expected ClickException")


def test_codex_evaluator_empty_output(monkeypatch, tmp_path):
    def fake_which(_name):
        return "/usr/bin/codex"

    def fake_run(cmd, **_kwargs):
        out_path = Path(cmd[cmd.index("--output-last-message") + 1])
        out_path.write_text("", encoding="utf-8")
        return _Proc(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(core.shutil, "which", fake_which)
    monkeypatch.setattr(core.subprocess, "run", fake_run)

    schema_path = tmp_path / "schema.json"
    schema_path.write_text("{}", encoding="utf-8")

    try:
        core._run_codex_changelog_evaluator(
            prompt="hi",
            schema_path=schema_path,
            cd=tmp_path,
            model="gpt",
        )
    except click.ClickException as exc:
        assert "codex output was empty" in str(exc)
    else:
        raise AssertionError("Expected ClickException")


def test_claude_evaluator_does_not_pass_prompt_in_argv(monkeypatch, tmp_path):
    seen: dict[str, object] = {}

    def fake_which(_name):
        return "/usr/bin/claude"

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["input"] = kwargs.get("input")
        payload = {"structured_output": {"summary": "ok"}, "is_error": False}
        return CompletedProcess(args=args, returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(core.shutil, "which", fake_which)
    monkeypatch.setattr(core.subprocess, "run", fake_run)

    prompt = "very large prompt body"
    result = core._run_claude_changelog_evaluator(
        prompt=prompt,
        json_schema={},
        cd=tmp_path,
        model="opus[1m]",
    )

    assert result == {"summary": "ok"}
    assert prompt not in seen["args"]
    assert seen["input"] == prompt


def test_claude_evaluator_defaults_to_opus_1m(monkeypatch):
    seen: dict[str, object] = {}

    def fake_which(_name):
        return "/usr/bin/claude"

    def fake_run(args, **_kwargs):
        seen["args"] = args
        payload = {"structured_output": {"summary": "ok"}, "is_error": False}
        return CompletedProcess(args=args, returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(core.shutil, "which", fake_which)
    monkeypatch.setattr(core.subprocess, "run", fake_run)

    core._run_claude_changelog_evaluator(prompt="hi", json_schema={})

    args = seen["args"]
    assert "--model" in args
    assert args[args.index("--model") + 1] == "opus[1m]"


def test_claude_evaluator_explicit_model_override(monkeypatch):
    seen: dict[str, object] = {}

    def fake_which(_name):
        return "/usr/bin/claude"

    def fake_run(args, **_kwargs):
        seen["args"] = args
        payload = {"structured_output": {"summary": "ok"}, "is_error": False}
        return CompletedProcess(args=args, returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(core.shutil, "which", fake_which)
    monkeypatch.setattr(core.subprocess, "run", fake_run)

    core._run_claude_changelog_evaluator(
        prompt="hi",
        json_schema={},
        model="claude-opus-4-6",
    )

    args = seen["args"]
    assert "--model" in args
    assert args[args.index("--model") + 1] == "claude-opus-4-6"


def test_changelog_prompt_artifact_paths_are_repo_local(tmp_path):
    full_path = core._changelog_prompt_artifact_path(
        project_root=tmp_path,
        run_id="run-123",
        variant="full",
    )
    assert full_path == tmp_path / ".tmp" / "changelog-eval" / "run-123-full-prompt.txt"


def test_changelog_prompt_artifact_path_sanitizes_components(tmp_path):
    full_path = core._changelog_prompt_artifact_path(
        project_root=tmp_path,
        run_id="../../run/../123",
        variant="../full\\bad",
    )
    assert full_path.parent == tmp_path / ".tmp" / "changelog-eval"
    assert full_path.name == "run_.._123-full_bad-prompt.txt"
    assert full_path.resolve().is_relative_to(tmp_path.resolve())


def test_write_changelog_prompt_artifact_writes_content(tmp_path):
    prompt = "prompt body"
    path = core._write_changelog_prompt_artifact(
        project_root=tmp_path,
        run_id="run-123",
        variant="full",
        prompt=prompt,
    )
    assert path.read_text(encoding="utf-8") == prompt


def test_archive_failed_changelog_prompt_moves_to_archive(tmp_path):
    src = tmp_path / ".tmp" / "changelog-eval" / "run-123-full-prompt.txt"
    src.parent.mkdir(parents=True)
    src.write_text("prompt", encoding="utf-8")

    archived = core._archive_failed_changelog_prompt(project_root=tmp_path, prompt_path=src)

    assert archived == tmp_path / ".archive" / "changelog-eval" / "run-123-full-prompt.txt"
    assert archived.read_text(encoding="utf-8") == "prompt"
    assert not src.exists()


def test_archive_failed_changelog_prompt_collision_keeps_both_files(tmp_path):
    first_src = tmp_path / ".tmp" / "changelog-eval" / "run-123-full-prompt.txt"
    first_src.parent.mkdir(parents=True)
    first_src.write_text("first prompt", encoding="utf-8")
    first_archived = core._archive_failed_changelog_prompt(project_root=tmp_path, prompt_path=first_src)

    second_src = tmp_path / ".tmp" / "changelog-eval" / "run-123-full-prompt.txt"
    second_src.write_text("second prompt", encoding="utf-8")
    second_archived = core._archive_failed_changelog_prompt(project_root=tmp_path, prompt_path=second_src)

    assert first_archived == tmp_path / ".archive" / "changelog-eval" / "run-123-full-prompt.txt"
    assert second_archived == tmp_path / ".archive" / "changelog-eval" / "run-123-full-prompt-1.txt"
    assert first_archived.read_text(encoding="utf-8") == "first prompt"
    assert second_archived.read_text(encoding="utf-8") == "second prompt"


def test_archive_failed_changelog_prompt_rejects_non_repo_path(tmp_path):
    external_root = tmp_path / "external"
    external_root.mkdir()
    external = external_root / "prompt.txt"
    external.write_text("prompt", encoding="utf-8")

    with pytest.raises(ValueError, match="project_root"):
        core._archive_failed_changelog_prompt(project_root=tmp_path, prompt_path=external)


def test_archive_failed_changelog_prompt_rejects_invalid_filename_under_temp_dir(tmp_path):
    invalid = tmp_path / ".tmp" / "changelog-eval" / "run--prompt.txt"
    invalid.parent.mkdir(parents=True)
    invalid.write_text("prompt", encoding="utf-8")

    with pytest.raises(ValueError, match="not a changelog temp artifact"):
        core._archive_failed_changelog_prompt(project_root=tmp_path, prompt_path=invalid)


def test_archive_failed_changelog_prompt_rejects_nested_descendant_under_temp_dir(tmp_path):
    nested = tmp_path / ".tmp" / "changelog-eval" / "nested" / "run-123-full-prompt.txt"
    nested.parent.mkdir(parents=True)
    nested.write_text("prompt", encoding="utf-8")

    with pytest.raises(ValueError, match="direct child"):
        core._archive_failed_changelog_prompt(project_root=tmp_path, prompt_path=nested)


def test_archive_failed_changelog_prompt_rejects_symlink_escaped_archive_destination(tmp_path):
    escaped_archive_root = tmp_path.parent / f"{tmp_path.name}-escaped-archive-root"
    escaped_archive_root.mkdir()
    (tmp_path / ".archive").symlink_to(escaped_archive_root, target_is_directory=True)
    escaped_archive_dir = escaped_archive_root / "changelog-eval"

    src = tmp_path / ".tmp" / "changelog-eval" / "run-123-full-prompt.txt"
    src.parent.mkdir(parents=True)
    src.write_text("prompt", encoding="utf-8")

    with pytest.raises(ValueError, match="project_root"):
        core._archive_failed_changelog_prompt(project_root=tmp_path, prompt_path=src)

    assert src.exists()
    assert not escaped_archive_dir.exists()
    assert not (escaped_archive_root / "changelog-eval" / src.name).exists()


def test_cleanup_changelog_prompt_artifact_rejects_non_repo_path(tmp_path):
    external_root = tmp_path / "external"
    external_root.mkdir()
    external = external_root / "run-123-full-prompt.txt"
    external.write_text("prompt", encoding="utf-8")

    with pytest.raises(ValueError, match="project_root"):
        core._cleanup_changelog_prompt_artifact(project_root=tmp_path / "repo", prompt_path=external)


def test_cleanup_changelog_prompt_artifact_rejects_non_temp_artifact_path(tmp_path):
    non_artifact = tmp_path / ".tmp" / "not-changelog-eval" / "run-123-full-prompt.txt"
    non_artifact.parent.mkdir(parents=True)
    non_artifact.write_text("prompt", encoding="utf-8")

    with pytest.raises(ValueError, match="changelog-eval"):
        core._cleanup_changelog_prompt_artifact(project_root=tmp_path, prompt_path=non_artifact)


def test_cleanup_changelog_prompt_artifact_rejects_invalid_filename_under_temp_dir(tmp_path):
    invalid = tmp_path / ".tmp" / "changelog-eval" / "run--prompt.txt"
    invalid.parent.mkdir(parents=True)
    invalid.write_text("prompt", encoding="utf-8")

    with pytest.raises(ValueError, match="not a changelog temp artifact"):
        core._cleanup_changelog_prompt_artifact(project_root=tmp_path, prompt_path=invalid)


def test_cleanup_changelog_prompt_artifact_rejects_nested_descendant_under_temp_dir(tmp_path):
    nested = tmp_path / ".tmp" / "changelog-eval" / "nested" / "run-123-full-prompt.txt"
    nested.parent.mkdir(parents=True)
    nested.write_text("prompt", encoding="utf-8")

    with pytest.raises(ValueError, match="direct child"):
        core._cleanup_changelog_prompt_artifact(project_root=tmp_path, prompt_path=nested)


def test_cleanup_changelog_prompt_artifact_is_idempotent_after_archive(tmp_path):
    src = tmp_path / ".tmp" / "changelog-eval" / "run-123-full-prompt.txt"
    src.parent.mkdir(parents=True)
    src.write_text("prompt", encoding="utf-8")
    core._archive_failed_changelog_prompt(project_root=tmp_path, prompt_path=src)

    core._cleanup_changelog_prompt_artifact(project_root=tmp_path, prompt_path=src)
    core._cleanup_changelog_prompt_artifact(project_root=tmp_path, prompt_path=src)

    assert not src.exists()


def test_claude_evaluator_structured_output(monkeypatch):
    def fake_which(_name):
        return "/usr/bin/claude"

    def fake_run(*_args, **_kwargs):
        payload = {"structured_output": {"summary": "ok"}, "is_error": False}
        return _Proc(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(core.shutil, "which", fake_which)
    monkeypatch.setattr(core.subprocess, "run", fake_run)

    result = core._run_claude_changelog_evaluator(prompt="hi", json_schema={})
    assert result["summary"] == "ok"


def test_claude_evaluator_result_json_fallback(monkeypatch):
    def fake_which(_name):
        return "/usr/bin/claude"

    def fake_run(*_args, **_kwargs):
        payload = {"result": '```json\n{"summary": "ok"}\n```', "is_error": False}
        return _Proc(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(core.shutil, "which", fake_which)
    monkeypatch.setattr(core.subprocess, "run", fake_run)

    result = core._run_claude_changelog_evaluator(prompt="hi", json_schema={})
    assert result["summary"] == "ok"


def test_claude_evaluator_is_error(monkeypatch):
    def fake_which(_name):
        return "/usr/bin/claude"

    def fake_run(*_args, **_kwargs):
        payload = {"structured_output": {"summary": "ok"}, "is_error": True}
        return _Proc(returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(core.shutil, "which", fake_which)
    monkeypatch.setattr(core.subprocess, "run", fake_run)

    try:
        core._run_claude_changelog_evaluator(prompt="hi", json_schema={})
    except click.ClickException as exc:
        assert "is_error=true" in str(exc)
    else:
        raise AssertionError("Expected ClickException")


def test_claude_evaluator_nonzero_exit(monkeypatch):
    def fake_which(_name):
        return "/usr/bin/claude"

    def fake_run(*_args, **_kwargs):
        return _Proc(returncode=1, stdout="out", stderr="err")

    monkeypatch.setattr(core.shutil, "which", fake_which)
    monkeypatch.setattr(core.subprocess, "run", fake_run)

    try:
        core._run_claude_changelog_evaluator(prompt="hi", json_schema={})
    except click.ClickException as exc:
        assert "claude failed" in str(exc)
        assert "stderr_tail" in str(exc)
    else:
        raise AssertionError("Expected ClickException")
