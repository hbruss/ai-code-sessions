"""Tests for changelog evaluator error handling in core."""

from pathlib import Path

import click

import ai_code_sessions as ai_code_sessions


def _write_minimal_files(tmp_path: Path):
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    source_jsonl = tmp_path / "source.jsonl"
    source_jsonl.write_text("{}", encoding="utf-8")
    source_match_json = tmp_path / "source_match.json"
    source_match_json.write_text("{}", encoding="utf-8")
    return session_dir, source_jsonl, source_match_json


def _mock_digest(**_kwargs):
    return {
        "delta": {
            "touched_files": {"created": [], "modified": [], "deleted": [], "moved": []},
            "tests": [],
            "commits": [],
        }
    }


def test_generate_and_append_unknown_evaluator_writes_failure(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir()
    session_dir, source_jsonl, source_match_json = _write_minimal_files(tmp_path)

    monkeypatch.setattr(ai_code_sessions, "_build_changelog_digest", _mock_digest)

    ok, run_id, status = ai_code_sessions._generate_and_append_changelog_entry(
        tool="codex",
        label="Test",
        cwd=str(project_root),
        project_root=project_root,
        session_dir=session_dir,
        start="2026-01-01T00:00:00+00:00",
        end="2026-01-01T01:00:00+00:00",
        source_jsonl=source_jsonl,
        source_match_json=source_match_json,
        actor="tester",
        evaluator="bogus",
    )

    assert ok is False
    assert status == "failed"
    failures_path = project_root / ".changelog" / "tester" / "failures.jsonl"
    assert failures_path.exists()
    assert "Unknown changelog evaluator" in failures_path.read_text(encoding="utf-8")
    assert run_id is not None


def test_generate_and_append_rate_limited_on_429(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir()
    session_dir, source_jsonl, source_match_json = _write_minimal_files(tmp_path)

    monkeypatch.setattr(ai_code_sessions, "_build_changelog_digest", _mock_digest)

    def fake_codex_eval(**_kwargs):
        raise click.ClickException("usage_limit_reached")

    monkeypatch.setattr(ai_code_sessions, "_run_codex_changelog_evaluator", fake_codex_eval)

    ok, run_id, status = ai_code_sessions._generate_and_append_changelog_entry(
        tool="codex",
        label="Test",
        cwd=str(project_root),
        project_root=project_root,
        session_dir=session_dir,
        start="2026-01-01T00:00:00+00:00",
        end="2026-01-01T01:00:00+00:00",
        source_jsonl=source_jsonl,
        source_match_json=source_match_json,
        actor="tester",
        evaluator="codex",
        halt_on_429=True,
    )

    assert ok is False
    assert status == "rate_limited"
    failures_path = project_root / ".changelog" / "tester" / "failures.jsonl"
    assert failures_path.exists()
    assert "usage_limit_reached" in failures_path.read_text(encoding="utf-8")
    assert run_id is not None
