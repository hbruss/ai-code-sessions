"""Tests for evaluator subprocess parsing and error handling."""

import json
from pathlib import Path

import click

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

    with click.testing.CliRunner().isolated_filesystem():
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
