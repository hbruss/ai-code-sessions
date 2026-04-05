"""Tests for changelog evaluator error handling in core."""

import json
import subprocess
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


def test_generate_and_append_detects_duplicate_against_sync_entry_source_identity(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir()
    session_dir, source_jsonl, _ = _write_minimal_files(tmp_path)

    start = "2026-01-01T00:00:00+00:00"
    end = "2026-01-01T01:00:00+00:00"
    identity = ai_code_sessions._canonical_session_identity_for_source(
        tool="codex",
        source_jsonl=source_jsonl,
        start=start,
        end=end,
    )

    entries_path = project_root / ".changelog" / "tester" / "entries.jsonl"
    entries_path.parent.mkdir(parents=True)
    entries_path.write_text(
        json.dumps(
            {
                "schema_version": ai_code_sessions.CHANGELOG_ENTRY_SCHEMA_VERSION,
                "run_id": "existing-sync-entry",
                "created_at": start,
                "tool": "codex",
                "actor": "tester",
                "project": project_root.name,
                "project_root": str(project_root),
                "label": "sync entry",
                "start": start,
                "end": end,
                "session_dir": str(source_jsonl.parent),
                "continuation_of_run_id": None,
                "transcript": {
                    "output_dir": None,
                    "index_html": None,
                    "source_jsonl": str(source_jsonl),
                    "source_match_json": None,
                },
                "source": {
                    "kind": "native_session",
                    "identity": identity,
                },
                "summary": "existing sync entry",
                "bullets": ["already synced"],
                "tags": [],
                "touched_files": {"created": [], "modified": [], "deleted": [], "moved": []},
                "tests": [],
                "commits": [],
                "notes": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(ai_code_sessions, "_build_changelog_digest", _mock_digest)
    monkeypatch.setattr(
        ai_code_sessions,
        "_run_codex_changelog_evaluator",
        lambda **_kwargs: {
            "summary": "updated sync entry",
            "bullets": ["updated via evaluator."],
            "tags": ["sync"],
            "notes": "rewritten",
        },
    )

    ok, run_id, status = ai_code_sessions._generate_and_append_changelog_entry(
        tool="codex",
        label="Test",
        cwd=str(project_root),
        project_root=project_root,
        session_dir=session_dir,
        start=start,
        end=end,
        source_jsonl=source_jsonl,
        source_match_json=None,
        actor="tester",
        evaluator="codex",
    )

    assert ok is True
    assert status == "updated"
    assert run_id == "existing-sync-entry"
    lines = entries_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["run_id"] == "existing-sync-entry"
    assert row["summary"] == "updated sync entry"
    assert row["bullets"] == ["updated via evaluator."]
    assert row["tags"] == ["sync"]


def test_generate_and_append_detects_duplicate_when_incoming_copy_maps_to_existing_native_entry(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir()
    session_dir = tmp_path / "session"
    session_dir.mkdir()

    start = "2026-01-01T00:00:00+00:00"
    end = "2026-01-01T01:00:00+00:00"
    native_source_jsonl = tmp_path / "native-rollout.jsonl"
    native_source_jsonl.write_text("{}", encoding="utf-8")
    copied_source_jsonl = tmp_path / "ctx-copy.jsonl"
    copied_source_jsonl.write_text("{}", encoding="utf-8")
    source_match_json = tmp_path / "source_match.json"
    source_match_json.write_text(
        json.dumps(
            {
                "best": {
                    "path": str(native_source_jsonl.resolve()),
                    "start": start,
                    "end": end,
                    "session_id": "sess-123",
                }
            }
        ),
        encoding="utf-8",
    )

    identity = ai_code_sessions._canonical_session_identity_for_source(
        tool="codex",
        source_jsonl=native_source_jsonl,
        start=start,
        end=end,
    )
    entries_path = project_root / ".changelog" / "tester" / "entries.jsonl"
    entries_path.parent.mkdir(parents=True)
    entries_path.write_text(
        json.dumps(
            {
                "schema_version": ai_code_sessions.CHANGELOG_ENTRY_SCHEMA_VERSION,
                "run_id": "existing-sync-entry",
                "created_at": start,
                "tool": "codex",
                "actor": "tester",
                "project": project_root.name,
                "project_root": str(project_root),
                "label": "sync entry",
                "start": start,
                "end": end,
                "session_dir": str(native_source_jsonl.parent),
                "continuation_of_run_id": None,
                "transcript": {
                    "output_dir": None,
                    "index_html": None,
                    "source_jsonl": str(native_source_jsonl),
                    "source_match_json": None,
                },
                "source": {
                    "kind": "native_session",
                    "identity": identity,
                },
                "summary": "existing sync entry",
                "bullets": ["already synced"],
                "tags": [],
                "touched_files": {"created": [], "modified": [], "deleted": [], "moved": []},
                "tests": [],
                "commits": [],
                "notes": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(ai_code_sessions, "_build_changelog_digest", _mock_digest)
    monkeypatch.setattr(
        ai_code_sessions,
        "_run_codex_changelog_evaluator",
        lambda **_kwargs: {
            "summary": "updated mapped sync entry",
            "bullets": ["updated from copied source."],
            "tags": ["sync"],
            "notes": "rewritten",
        },
    )

    ok, run_id, status = ai_code_sessions._generate_and_append_changelog_entry(
        tool="codex",
        label="Test",
        cwd=str(project_root),
        project_root=project_root,
        session_dir=session_dir,
        start=start,
        end=end,
        source_jsonl=copied_source_jsonl,
        source_match_json=source_match_json,
        actor="tester",
        evaluator="codex",
    )

    assert ok is True
    assert status == "updated"
    assert run_id == "existing-sync-entry"
    lines = entries_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["run_id"] == "existing-sync-entry"
    assert row["summary"] == "updated mapped sync entry"
    assert row["bullets"] == ["updated from copied source."]
    assert row["tags"] == ["sync"]


def test_generate_and_append_detects_duplicate_against_ctx_entry_by_rereading_transcript(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir()
    session_dir = tmp_path / "session"
    session_dir.mkdir()

    start = "2026-01-01T00:00:00+00:00"
    end = "2026-01-01T01:00:00+00:00"
    session_rows = [
        {
            "type": "session_meta",
            "timestamp": start,
            "payload": {
                "timestamp": start,
                "cwd": str(project_root),
                "id": "sess-123",
            },
        },
        {
            "type": "event_msg",
            "timestamp": end,
        },
    ]
    source_jsonl = tmp_path / "native-rollout.jsonl"
    source_jsonl.write_text("\n".join(json.dumps(row) for row in session_rows) + "\n", encoding="utf-8")
    copied_source_jsonl = tmp_path / "ctx-copy.jsonl"
    copied_source_jsonl.write_text("\n".join(json.dumps(row) for row in session_rows) + "\n", encoding="utf-8")

    entries_path = project_root / ".changelog" / "tester" / "entries.jsonl"
    entries_path.parent.mkdir(parents=True)
    entries_path.write_text(
        json.dumps(
            {
                "schema_version": ai_code_sessions.CHANGELOG_ENTRY_SCHEMA_VERSION,
                "run_id": "existing-ctx-entry",
                "created_at": start,
                "tool": "codex",
                "actor": "tester",
                "project": project_root.name,
                "project_root": str(project_root),
                "label": "ctx export",
                "start": start,
                "end": end,
                "session_dir": str(session_dir),
                "continuation_of_run_id": None,
                "transcript": {
                    "output_dir": str(session_dir),
                    "index_html": str(session_dir / "index.html"),
                    "source_jsonl": str(copied_source_jsonl),
                    "source_match_json": None,
                },
                "summary": "existing ctx entry",
                "bullets": ["already exported"],
                "tags": [],
                "touched_files": {"created": [], "modified": [], "deleted": [], "moved": []},
                "tests": [],
                "commits": [],
                "notes": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    native_candidate = {
        "tool": "codex",
        "source_jsonl": str(source_jsonl.resolve()),
        "start": start,
        "end": end,
        "session_id": "sess-123",
        "cwd": str(project_root),
        "prompt_summary": "",
        "source": {
            "kind": "native_session",
            "identity": ai_code_sessions._canonical_session_identity_for_source(
                tool="codex",
                source_jsonl=source_jsonl,
                start=start,
                end=end,
            ),
        },
    }

    monkeypatch.setattr(
        ai_code_sessions,
        "_discover_native_sessions",
        lambda **_: [native_candidate],
    )
    monkeypatch.setattr(
        ai_code_sessions,
        "_build_changelog_digest",
        lambda **_: (_ for _ in ()).throw(AssertionError("duplicate detection should stop before evaluator work")),
    )

    ok, run_id, status = ai_code_sessions._generate_and_append_changelog_entry(
        tool="codex",
        label="Test",
        cwd=str(project_root),
        project_root=project_root,
        session_dir=session_dir,
        start=start,
        end=end,
        source_jsonl=source_jsonl,
        source_match_json=None,
        actor="tester",
        evaluator="codex",
    )

    assert ok is False
    assert status == "exists"
    assert run_id is not None
    assert len(entries_path.read_text(encoding="utf-8").splitlines()) == 1


def test_generate_and_append_claude_full_prompt_success_cleans_temp_artifact(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir()
    session_dir, source_jsonl, source_match_json = _write_minimal_files(tmp_path)

    monkeypatch.setattr(ai_code_sessions, "_build_changelog_digest", _mock_digest)

    def fake_claude_eval(**kwargs):
        full_paths = list((project_root / ".tmp" / "changelog-eval").glob("*-full-prompt.txt"))
        assert len(full_paths) == 1
        assert '"digest_mode": "budget"' not in full_paths[0].read_text(encoding="utf-8")
        assert '"digest_mode": "budget"' not in kwargs["prompt"]
        return {"summary": "ok", "bullets": ["did work."], "tags": []}

    monkeypatch.setattr(ai_code_sessions, "_run_claude_changelog_evaluator", fake_claude_eval)

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
        evaluator="claude",
    )

    assert ok is True
    assert status == "appended"
    assert run_id is not None
    full_temp = project_root / ".tmp" / "changelog-eval" / f"{run_id}-full-prompt.txt"
    full_archive = project_root / ".archive" / "changelog-eval" / f"{run_id}-full-prompt.txt"
    assert not full_temp.exists()
    assert not full_archive.exists()


def test_generate_and_append_claude_timeout_archives_full_and_retries_budget(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir()
    session_dir, source_jsonl, source_match_json = _write_minimal_files(tmp_path)
    call_order: list[str] = []

    monkeypatch.setattr(ai_code_sessions, "_build_changelog_digest", _mock_digest)

    def fake_claude_eval(**kwargs):
        prompt = kwargs["prompt"]
        mode = "budget" if '"digest_mode": "budget"' in prompt else "full"
        call_order.append(mode)
        if mode == "full":
            raise subprocess.TimeoutExpired(cmd=["claude"], timeout=30)
        return {"summary": "ok", "bullets": ["did work."], "tags": []}

    monkeypatch.setattr(ai_code_sessions, "_run_claude_changelog_evaluator", fake_claude_eval)

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
        evaluator="claude",
    )

    assert ok is True
    assert status == "appended"
    assert run_id is not None
    assert call_order == ["full", "budget"]

    full_temp = project_root / ".tmp" / "changelog-eval" / f"{run_id}-full-prompt.txt"
    full_archive = project_root / ".archive" / "changelog-eval" / f"{run_id}-full-prompt.txt"
    budget_temp = project_root / ".tmp" / "changelog-eval" / f"{run_id}-budget-prompt.txt"
    assert not full_temp.exists()
    assert '"digest_mode": "budget"' not in full_archive.read_text(encoding="utf-8")
    assert not budget_temp.exists()


def test_generate_and_append_claude_budget_retry_failure_archives_budget_prompt(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir()
    session_dir, source_jsonl, source_match_json = _write_minimal_files(tmp_path)

    monkeypatch.setattr(ai_code_sessions, "_build_changelog_digest", _mock_digest)

    def fake_claude_eval(**kwargs):
        prompt = kwargs["prompt"]
        mode = "budget" if '"digest_mode": "budget"' in prompt else "full"
        if mode == "full":
            raise click.ClickException("context window ran out of room")
        raise RuntimeError("still failing")

    monkeypatch.setattr(ai_code_sessions, "_run_claude_changelog_evaluator", fake_claude_eval)

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
        evaluator="claude",
    )

    assert ok is False
    assert status == "failed"
    assert run_id is not None

    full_archive = project_root / ".archive" / "changelog-eval" / f"{run_id}-full-prompt.txt"
    budget_archive = project_root / ".archive" / "changelog-eval" / f"{run_id}-budget-prompt.txt"
    full_temp = project_root / ".tmp" / "changelog-eval" / f"{run_id}-full-prompt.txt"
    budget_temp = project_root / ".tmp" / "changelog-eval" / f"{run_id}-budget-prompt.txt"
    assert '"digest_mode": "budget"' not in full_archive.read_text(encoding="utf-8")
    assert '"digest_mode": "budget"' in budget_archive.read_text(encoding="utf-8")
    assert not full_temp.exists()
    assert not budget_temp.exists()


def test_generate_and_append_claude_non_retryable_first_failure_archives_full_prompt(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir()
    session_dir, source_jsonl, source_match_json = _write_minimal_files(tmp_path)

    monkeypatch.setattr(ai_code_sessions, "_build_changelog_digest", _mock_digest)

    def fake_claude_eval(**_kwargs):
        raise RuntimeError("boom non-retryable")

    monkeypatch.setattr(ai_code_sessions, "_run_claude_changelog_evaluator", fake_claude_eval)

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
        evaluator="claude",
    )

    assert ok is False
    assert status == "failed"
    assert run_id is not None

    full_temp = project_root / ".tmp" / "changelog-eval" / f"{run_id}-full-prompt.txt"
    full_archive = project_root / ".archive" / "changelog-eval" / f"{run_id}-full-prompt.txt"
    assert not full_temp.exists()
    assert full_archive.exists()
    assert '"digest_mode": "budget"' not in full_archive.read_text(encoding="utf-8")
