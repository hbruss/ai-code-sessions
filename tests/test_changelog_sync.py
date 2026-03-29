import json
from datetime import datetime, timezone
from pathlib import Path

import ai_code_sessions as core


def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return path


def _write_codex_session(
    sessions_root: Path,
    *,
    filename: str,
    start: str,
    end: str,
    cwd: Path | None,
    session_id: str,
) -> Path:
    start_dt = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(timezone.utc)
    day_dir = sessions_root / f"{start_dt.year:04d}" / f"{start_dt.month:02d}" / f"{start_dt.day:02d}"
    return _write_jsonl(
        day_dir / filename,
        [
            {
                "type": "session_meta",
                "timestamp": start,
                "payload": {
                    "timestamp": start,
                    "cwd": str(cwd) if cwd is not None else None,
                    "id": session_id,
                },
            },
            {
                "type": "user",
                "timestamp": start,
                "message": {"content": [{"type": "text", "text": f"Prompt for {session_id}"}]},
            },
            {"type": "event_msg", "timestamp": end},
        ],
    )


def _write_claude_session(
    home_root: Path,
    *,
    project_root: Path,
    filename: str,
    start: str,
    end: str,
    cwd: Path | None,
    session_id: str,
) -> Path:
    encoded_dir = home_root / ".claude" / "projects" / core._encode_claude_project_folder(str(project_root))
    return _write_jsonl(
        encoded_dir / filename,
        [
            {
                "timestamp": start,
                "cwd": str(cwd) if cwd is not None else None,
                "sessionId": session_id,
            },
            {"timestamp": end, "cwd": str(cwd) if cwd is not None else None, "sessionId": session_id},
        ],
    )


def test_native_session_identity_uses_tool_session_and_time_bounds(tmp_path):
    path = tmp_path / "rollout-abc.jsonl"
    identity = core._canonical_session_identity_for_source(
        tool="codex",
        source_jsonl=path,
        start="2026-01-01T00:00:00Z",
        end="2026-01-01T00:05:00.000000Z",
    )

    assert identity["tool"] == "codex"
    assert identity["native_source_path"] == str(path.resolve())
    assert identity["start"] == "2026-01-01T00:00:00+00:00"
    assert identity["end"] == "2026-01-01T00:05:00+00:00"


def test_native_session_identity_normalizes_equivalent_timestamp_formats(tmp_path):
    path = tmp_path / "rollout-abc.jsonl"
    identity_from_z = core._canonical_session_identity_for_source(
        tool="codex",
        source_jsonl=path,
        start="2026-01-01T00:00:00Z",
        end="2026-01-01T00:05:00.0Z",
    )
    identity_from_offset = core._canonical_session_identity_for_source(
        tool="codex",
        source_jsonl=path,
        start="2026-01-01T00:00:00+00:00",
        end="2026-01-01T00:05:00.000000+00:00",
    )

    assert identity_from_z == identity_from_offset


def test_entry_session_identity_recanonicalizes_source_identity_and_allows_null_transcript_fields(tmp_path):
    path = tmp_path / "rollout-abc.jsonl"
    entry = {
        "tool": "codex",
        "start": "2026-01-01T00:00:00Z",
        "end": "2026-01-01T00:05:00.000000Z",
        "transcript": {
            "output_dir": None,
            "index_html": None,
            "source_jsonl": str(path),
            "source_match_json": None,
        },
        "source": {
            "kind": "native_session",
            "identity": {
                "tool": "CODEX",
                "native_source_path": str(path),
                "start": "2026-01-01T00:00:00.000000+00:00",
                "end": "2026-01-01T00:05:00Z",
            },
        },
    }

    identity = core._entry_session_identity(entry)

    assert identity == core._canonical_session_identity_for_source(
        tool="codex",
        source_jsonl=path,
        start="2026-01-01T00:00:00+00:00",
        end="2026-01-01T00:05:00+00:00",
    )


def test_generate_and_append_changelog_entry_records_source_metadata(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir()
    session_dir = tmp_path / ".codex" / "sessions" / "session-1"
    session_dir.mkdir(parents=True)

    source_jsonl = tmp_path / "rollout-abc.jsonl"
    source_jsonl.write_text("{}", encoding="utf-8")
    source_match_json = tmp_path / "source_match.json"
    source_match_json.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        core,
        "_build_changelog_digest",
        lambda **_: {
            "delta": {
                "touched_files": {"created": [], "modified": [], "deleted": [], "moved": []},
                "tests": [],
                "commits": [],
            }
        },
    )
    monkeypatch.setattr(
        core,
        "_run_codex_changelog_evaluator",
        lambda **_: {"summary": "ok", "bullets": ["did thing"], "tags": [], "notes": None},
    )

    ok, _, status = core._generate_and_append_changelog_entry(
        tool="codex",
        label="Test",
        cwd=str(project_root),
        project_root=project_root,
        session_dir=session_dir,
        start="2026-01-01T00:00:00+00:00",
        end="2026-01-01T00:05:00+00:00",
        source_jsonl=source_jsonl,
        source_match_json=source_match_json,
        actor="tester",
        evaluator="codex",
        evaluator_model=None,
        claude_max_thinking_tokens=None,
        continuation_of_run_id=None,
        halt_on_429=False,
    )

    assert ok is True
    assert status == "appended"

    entries_path = project_root / ".changelog" / "tester" / "entries.jsonl"
    entry = json.loads(entries_path.read_text(encoding="utf-8").strip())
    assert entry["source"]["kind"] == "native_session"
    assert entry["source"]["identity"] == core._canonical_session_identity_for_source(
        tool="codex",
        source_jsonl=source_jsonl,
        start="2026-01-01T00:00:00+00:00",
        end="2026-01-01T00:05:00+00:00",
    )


def test_preview_changelog_append_status_reports_existing_entries(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    project_root.mkdir()
    session_dir = tmp_path / ".codex" / "sessions" / "session-1"
    session_dir.mkdir(parents=True)

    source_jsonl = tmp_path / "rollout-abc.jsonl"
    source_jsonl.write_text("{}", encoding="utf-8")
    source_match_json = tmp_path / "source_match.json"
    source_match_json.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        core,
        "_build_changelog_digest",
        lambda **_: {
            "delta": {
                "touched_files": {"created": [], "modified": [], "deleted": [], "moved": []},
                "tests": [],
                "commits": [],
            }
        },
    )
    monkeypatch.setattr(
        core,
        "_run_codex_changelog_evaluator",
        lambda **_: {"summary": "ok", "bullets": ["did thing"], "tags": [], "notes": None},
    )

    ok, run_id, status = core._generate_and_append_changelog_entry(
        tool="codex",
        label="Test",
        cwd=str(project_root),
        project_root=project_root,
        session_dir=session_dir,
        start="2026-01-01T00:00:00+00:00",
        end="2026-01-01T00:05:00+00:00",
        source_jsonl=source_jsonl,
        source_match_json=source_match_json,
        actor="tester",
        evaluator="codex",
        evaluator_model=None,
        claude_max_thinking_tokens=None,
        continuation_of_run_id=None,
        halt_on_429=False,
    )

    assert ok is True
    assert status == "appended"

    preview_run_id, preview_status = core._preview_changelog_append_status(
        tool="codex",
        project_root=project_root,
        session_dir=session_dir,
        start="2026-01-01T00:00:00+00:00",
        end="2026-01-01T00:05:00+00:00",
        source_jsonl=source_jsonl,
        source_match_json=source_match_json,
        actor="tester",
    )

    assert preview_run_id == run_id
    assert preview_status == "exists"


def test_discover_native_sessions_filters_to_overlapping_window_and_sorts_newest_first(tmp_path, monkeypatch):
    codex_root = tmp_path / ".codex" / "sessions"
    home_root = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()

    monkeypatch.setattr(core._core, "_user_codex_sessions_dir", lambda: codex_root)
    monkeypatch.setattr(core._core.Path, "home", classmethod(lambda cls: home_root))

    _write_codex_session(
        codex_root,
        filename="rollout-too-early.jsonl",
        start="2026-01-01T00:00:00Z",
        end="2026-01-01T00:10:00Z",
        cwd=repo,
        session_id="codex-too-early",
    )
    _write_codex_session(
        codex_root,
        filename="rollout-overlap.jsonl",
        start="2026-01-01T00:15:00Z",
        end="2026-01-01T01:15:00Z",
        cwd=repo,
        session_id="codex-overlap",
    )
    newest_codex = _write_codex_session(
        codex_root,
        filename="rollout-newest.jsonl",
        start="2026-01-01T02:20:00Z",
        end="2026-01-01T03:00:00Z",
        cwd=repo,
        session_id="codex-newest",
    )
    claude_path = _write_claude_session(
        home_root,
        project_root=repo,
        filename="claude-overlap.jsonl",
        start="2026-01-01T01:10:00Z",
        end="2026-01-01T02:15:00Z",
        cwd=repo,
        session_id="claude-overlap",
    )
    _write_claude_session(
        home_root,
        project_root=repo,
        filename="claude-too-late.jsonl",
        start="2026-01-01T03:10:00Z",
        end="2026-01-01T03:25:00Z",
        cwd=repo,
        session_id="claude-too-late",
    )

    sessions = core._discover_native_sessions(
        tools=("codex", "claude"),
        since=datetime(2026, 1, 1, 0, 30, tzinfo=timezone.utc),
        until=datetime(2026, 1, 1, 2, 30, tzinfo=timezone.utc),
    )

    assert [Path(session["source_jsonl"]).name for session in sessions] == [
        newest_codex.name,
        claude_path.name,
        "rollout-overlap.jsonl",
    ]
    assert [session["tool"] for session in sessions] == ["codex", "claude", "codex"]
    assert sessions[0]["end"] == "2026-01-01T03:00:00+00:00"
    assert sessions[1]["end"] == "2026-01-01T02:15:00+00:00"
    assert sessions[2]["end"] == "2026-01-01T01:15:00+00:00"


def test_discover_native_codex_and_claude_sessions_support_tool_specific_paths(tmp_path, monkeypatch):
    codex_root = tmp_path / ".codex" / "sessions"
    home_root = tmp_path / "home"
    codex_repo = tmp_path / "codex-repo"
    claude_repo = tmp_path / "claude-repo"
    codex_repo.mkdir()
    claude_repo.mkdir()

    monkeypatch.setattr(core._core, "_user_codex_sessions_dir", lambda: codex_root)
    monkeypatch.setattr(core._core.Path, "home", classmethod(lambda cls: home_root))

    codex_path = _write_codex_session(
        codex_root,
        filename="rollout-codex-only.jsonl",
        start="2026-01-02T12:00:00Z",
        end="2026-01-02T12:20:00Z",
        cwd=codex_repo,
        session_id="codex-only",
    )
    claude_path = _write_claude_session(
        home_root,
        project_root=claude_repo,
        filename="claude-only.jsonl",
        start="2026-01-02T13:00:00Z",
        end="2026-01-02T13:25:00Z",
        cwd=claude_repo,
        session_id="claude-only",
    )

    since = datetime(2026, 1, 2, 11, 0, tzinfo=timezone.utc)
    until = datetime(2026, 1, 2, 14, 0, tzinfo=timezone.utc)

    codex_sessions = core._discover_native_codex_sessions(since=since, until=until)
    claude_sessions = core._discover_native_claude_sessions(since=since, until=until)

    assert [(session["tool"], Path(session["source_jsonl"]).name) for session in codex_sessions] == [
        ("codex", codex_path.name)
    ]
    assert [(session["tool"], Path(session["source_jsonl"]).name) for session in claude_sessions] == [
        ("claude", claude_path.name)
    ]


def test_discover_native_codex_sessions_extracts_prompt_summary_from_response_item_user_messages(tmp_path, monkeypatch):
    codex_root = tmp_path / ".codex" / "sessions"
    home_root = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()

    monkeypatch.setattr(core._core, "_user_codex_sessions_dir", lambda: codex_root)
    monkeypatch.setattr(core._core.Path, "home", classmethod(lambda cls: home_root))

    start = "2026-01-03T12:00:00Z"
    end = "2026-01-03T12:05:00Z"
    start_dt = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(timezone.utc)
    day_dir = codex_root / f"{start_dt.year:04d}" / f"{start_dt.month:02d}" / f"{start_dt.day:02d}"
    session_path = _write_jsonl(
        day_dir / "rollout-codex-summary.jsonl",
        [
            {
                "type": "session_meta",
                "timestamp": start,
                "payload": {
                    "timestamp": start,
                    "cwd": str(repo),
                    "id": "codex-summary",
                },
            },
            {
                "type": "response_item",
                "timestamp": start,
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "Center yourself on the repo to refamiliarize yourself. I want to review a few things when you're done.",
                        }
                    ],
                },
            },
            {"type": "event_msg", "timestamp": end},
        ],
    )

    sessions = core._discover_native_codex_sessions(
        since=datetime(2026, 1, 3, 11, 0, tzinfo=timezone.utc),
        until=datetime(2026, 1, 3, 13, 0, tzinfo=timezone.utc),
    )

    assert [(session["tool"], Path(session["source_jsonl"]).name) for session in sessions] == [
        ("codex", session_path.name)
    ]
    assert sessions[0]["prompt_summary"] == (
        "Center yourself on the repo to refamiliarize yourself. I want to review a few things when you're done."
    )


def test_discover_native_codex_sessions_prefers_event_user_message_over_instruction_wrapper(tmp_path, monkeypatch):
    codex_root = tmp_path / ".codex" / "sessions"
    home_root = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()

    monkeypatch.setattr(core._core, "_user_codex_sessions_dir", lambda: codex_root)
    monkeypatch.setattr(core._core.Path, "home", classmethod(lambda cls: home_root))

    start = "2026-01-03T13:00:00Z"
    end = "2026-01-03T13:05:00Z"
    start_dt = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(timezone.utc)
    day_dir = codex_root / f"{start_dt.year:04d}" / f"{start_dt.month:02d}" / f"{start_dt.day:02d}"
    session_path = _write_jsonl(
        day_dir / "rollout-codex-event-summary.jsonl",
        [
            {
                "type": "session_meta",
                "timestamp": start,
                "payload": {
                    "timestamp": start,
                    "cwd": str(repo),
                    "id": "codex-event-summary",
                },
            },
            {
                "type": "response_item",
                "timestamp": start,
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "# AGENTS.md instructions for /tmp/repo\n\n<INSTRUCTIONS>\nRepository rules...\n",
                        }
                    ],
                },
            },
            {
                "type": "event_msg",
                "timestamp": start,
                "payload": {
                    "type": "user_message",
                    "message": "Center yourself on the repo to refamiliarize yourself. I want to review a few things when you're done.",
                },
            },
            {"type": "event_msg", "timestamp": end},
        ],
    )

    sessions = core._discover_native_codex_sessions(
        since=datetime(2026, 1, 3, 12, 0, tzinfo=timezone.utc),
        until=datetime(2026, 1, 3, 14, 0, tzinfo=timezone.utc),
    )

    assert [(session["tool"], Path(session["source_jsonl"]).name) for session in sessions] == [
        ("codex", session_path.name)
    ]
    assert sessions[0]["prompt_summary"] == (
        "Center yourself on the repo to refamiliarize yourself. I want to review a few things when you're done."
    )


def test_resolve_native_session_project_returns_high_confidence_with_consistent_git_evidence(tmp_path, monkeypatch):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    cwd = repo_root / "subdir"
    cwd.mkdir()
    source_jsonl = tmp_path / "rollout.jsonl"
    source_jsonl.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        core._core,
        "_git_toplevel",
        lambda path: repo_root if Path(path) == cwd else None,
    )

    resolution = core._resolve_native_session_project(
        {
            "tool": "codex",
            "cwd": str(cwd),
            "source_jsonl": source_jsonl,
            "start": "2026-01-01T00:00:00+00:00",
            "end": "2026-01-01T00:05:00+00:00",
            "session_id": "codex-123",
            "prompt_summary": "Add changelog sync",
        }
    )

    assert resolution["confidence"] == "high"
    assert resolution["project_root"] == str(repo_root)
    assert resolution["reason"] == "cwd resolves to a git toplevel with consistent evidence"
    assert resolution["evidence"] == {
        "tool": "codex",
        "source_jsonl": str(source_jsonl.resolve()),
        "session_id": "codex-123",
        "start": "2026-01-01T00:00:00+00:00",
        "end": "2026-01-01T00:05:00+00:00",
        "cwd": str(cwd.resolve()),
        "git_toplevel": str(repo_root.resolve()),
        "prompt_summary": "Add changelog sync",
        "project_hints": [],
        "plausible_project_roots": [str(repo_root.resolve())],
        "conflicts": [],
    }


def test_resolve_native_session_project_returns_low_confidence_without_repo_evidence(tmp_path):
    source_jsonl = tmp_path / "session.jsonl"
    source_jsonl.write_text("{}", encoding="utf-8")

    resolution = core._resolve_native_session_project(
        {
            "tool": "claude",
            "cwd": None,
            "source_jsonl": source_jsonl,
            "start": "2026-01-01T00:00:00+00:00",
            "end": "2026-01-01T00:05:00+00:00",
            "session_id": "claude-123",
            "prompt_summary": "Investigate sync failures",
        }
    )

    assert resolution["confidence"] == "low"
    assert resolution["project_root"] is None
    assert resolution["reason"] == "No trustworthy repo evidence found"
    assert resolution["evidence"] == {
        "tool": "claude",
        "source_jsonl": str(source_jsonl.resolve()),
        "session_id": "claude-123",
        "start": "2026-01-01T00:00:00+00:00",
        "end": "2026-01-01T00:05:00+00:00",
        "cwd": None,
        "git_toplevel": None,
        "prompt_summary": "Investigate sync failures",
        "project_hints": [],
        "plausible_project_roots": [],
        "conflicts": [],
    }


def test_resolve_native_session_project_returns_medium_without_selecting_conflicting_roots(tmp_path, monkeypatch):
    cwd_repo = tmp_path / "cwd-repo"
    hinted_repo = tmp_path / "hinted-repo"
    cwd = cwd_repo / "subdir"
    cwd.mkdir(parents=True)
    hinted_repo.mkdir()
    source_jsonl = tmp_path / "session.jsonl"
    source_jsonl.write_text("{}", encoding="utf-8")

    def fake_git_toplevel(path: Path) -> Path | None:
        resolved = Path(path).resolve()
        if resolved == cwd.resolve():
            return cwd_repo
        if resolved == hinted_repo.resolve():
            return hinted_repo
        return None

    monkeypatch.setattr(core._core, "_git_toplevel", fake_git_toplevel)

    resolution = core._resolve_native_session_project(
        {
            "tool": "claude",
            "cwd": str(cwd),
            "project_root_hint": str(hinted_repo),
            "source_jsonl": source_jsonl,
            "start": "2026-01-01T00:00:00+00:00",
            "end": "2026-01-01T00:05:00+00:00",
            "session_id": "claude-conflict",
            "prompt_summary": "Investigate repo mapping",
        }
    )

    assert resolution["confidence"] == "medium"
    assert resolution["project_root"] is None
    assert resolution["reason"] == "Multiple plausible repos found; user selection is required"
    assert resolution["evidence"]["plausible_project_roots"] == [
        str(cwd_repo.resolve()),
        str(hinted_repo.resolve()),
    ]
    assert resolution["evidence"]["conflicts"] == [
        "Multiple plausible git toplevels were derived from session evidence"
    ]


def test_resolve_native_session_project_returns_medium_without_auto_selecting_single_plausible_root(
    tmp_path, monkeypatch
):
    hinted_repo = tmp_path / "hinted-repo"
    hinted_repo.mkdir()
    source_jsonl = tmp_path / "session.jsonl"
    source_jsonl.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        core._core,
        "_git_toplevel",
        lambda path: hinted_repo if Path(path).resolve() == hinted_repo.resolve() else None,
    )

    resolution = core._resolve_native_session_project(
        {
            "tool": "claude",
            "cwd": None,
            "project_root_hint": str(hinted_repo),
            "source_jsonl": source_jsonl,
            "start": "2026-01-01T00:00:00+00:00",
            "end": "2026-01-01T00:05:00+00:00",
            "session_id": "claude-medium",
            "prompt_summary": "Need operator confirmation",
        }
    )

    assert resolution["confidence"] == "medium"
    assert resolution["project_root"] is None
    assert resolution["reason"] == "Repo evidence is plausible but not strong enough for automatic writes"
    assert resolution["evidence"]["plausible_project_roots"] == [str(hinted_repo.resolve())]
