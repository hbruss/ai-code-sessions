"""Hermes native-export ingestion at the public parsing and sync boundaries."""

import json
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

import ai_code_sessions.core as core


def test_parse_hermes_export_retains_compacted_history_and_tool_results(tmp_path):
    source = tmp_path / "hermes.jsonl"
    source.write_text(
        json.dumps(
            {
                "id": "session-one",
                "profile_name": "research",
                "transport_profile": "default",
                "source": "discord",
                "started_at": 1767225600.0,
                "ended_at": None,
                "cwd": None,
                "git_repo_root": None,
                "messages": [
                    {
                        "id": 1,
                        "message_uid": "prompt-one",
                        "role": "user",
                        "content": "Fix the failing import.",
                        "timestamp": 1767225601.0,
                        "active": 0,
                        "compacted": 1,
                    },
                    {
                        "id": 2,
                        "message_uid": "call-one",
                        "role": "assistant",
                        "content": "Checking the failure.",
                        "timestamp": 1767225602.0,
                        "tool_calls": [
                            {
                                "id": "terminal-one",
                                "type": "function",
                                "function": {"name": "terminal", "arguments": '{"command":"pytest -q"}'},
                            }
                        ],
                        "active": 1,
                        "compacted": 0,
                    },
                    {
                        "id": 3,
                        "message_uid": "result-one",
                        "role": "tool",
                        "tool_call_id": "terminal-one",
                        "tool_name": "terminal",
                        "content": '{"output":"ImportError: missing module","exit_code":1,"error":null}',
                        "timestamp": 1767225603.0,
                        "active": 1,
                        "compacted": 0,
                    },
                    {
                        "id": 4,
                        "role": "assistant",
                        "content": "This reverted response must not become an accomplishment.",
                        "timestamp": 1767225604.0,
                        "active": 0,
                        "compacted": 0,
                    },
                    {"id": 5, "role": "session_meta", "content": None, "timestamp": 1767225605.0},
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    parsed = core.parse_session_file(source)
    lines = parsed["loglines"]
    assert [line["type"] for line in lines] == ["user", "assistant", "assistant"]
    assert core.extract_text_from_content(lines[0]["message"]["content"]) == "Fix the failing import."
    tool_use = next(block for block in lines[1]["message"]["content"] if block["type"] == "tool_use")
    assert tool_use["name"] == "terminal"
    assert tool_use["input"] == {"command": "pytest -q"}
    tool_result = lines[2]["message"]["content"][0]
    assert tool_result["tool_use_id"] == tool_use["id"] == "terminal-one"
    assert tool_result["is_error"] is True
    assert "ImportError: missing module" in tool_result["content"]


def _hermes_message(uid, role, content, timestamp, **metadata):
    return {
        "message_uid": uid,
        "role": role,
        "content": content,
        "timestamp": timestamp,
        "active": 1,
        "compacted": 0,
        **metadata,
    }


def _hermes_session(
    session_id,
    *,
    profile="research",
    parent=None,
    source="discord",
    started_at=1767225600.0,
    ended_at=None,
    end_reason=None,
    model_config=None,
    messages=None,
    cwd=None,
    git_repo_root=None,
    title="Synthetic session",
    hidden=0,
    transport_profile="default",
):
    return {
        "id": session_id,
        "profile_name": profile,
        "transport_profile": transport_profile,
        "source": source,
        "started_at": started_at,
        "ended_at": ended_at,
        "parent_session_id": parent,
        "end_reason": end_reason,
        "model_config": json.dumps(model_config or {}),
        "messages": list(messages or []),
        "cwd": cwd,
        "git_repo_root": git_repo_root,
        "title": title,
        "hidden": hidden,
    }


def _write_hermes_export(path, sessions):
    raw = "".join(json.dumps(session, ensure_ascii=False) + "\n" for session in sessions).encode("utf-8")
    path.write_bytes(raw)
    return path, raw


def _fake_hermes(tmp_path, body):
    executable = tmp_path / "fake-hermes"
    executable.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    executable.chmod(0o700)
    return executable


def _install_synthetic_evaluator(monkeypatch):
    def evaluate(**_kwargs):
        return {
            "summary": "Synthetic Hermes work",
            "bullets": ["Updated the synthetic session."],
            "tags": ["synthetic"],
            "notes": None,
        }

    monkeypatch.setattr(core, "_run_codex_changelog_evaluator", evaluate)
    package = sys.modules.get("ai_code_sessions")
    if package is not None:
        monkeypatch.setattr(package, "_run_codex_changelog_evaluator", evaluate, raising=False)


def test_hermes_candidate_timestamps_and_compacted_content_preserve_raw_evidence(tmp_path, monkeypatch):
    export_dir = tmp_path / "private-exports"
    monkeypatch.setenv("CTX_HERMES_EXPORTS_DIR", str(export_dir))
    epoch = 1767225600.0
    session = _hermes_session(
        "timestamp-root",
        title="",
        started_at=epoch,
        ended_at=epoch + 1,
        messages=[
            _hermes_message(
                "prompt-uid",
                "user",
                "The compacted prompt predates the session clock.",
                epoch - 2,
                active=0,
                compacted=1,
            ),
            _hermes_message(
                "summary-uid",
                "assistant",
                "A compact summary remains eligible.",
                epoch + 2,
                active=0,
                compacted=1,
                absorbed_message_uids=["prompt-uid"],
            ),
            _hermes_message(
                "undone-uid",
                "assistant",
                "Undone text must not enter the digest.",
                epoch + 100,
                active=0,
                compacted=0,
            ),
            _hermes_message(
                "hidden-uid",
                "user",
                "Hidden display content is not a prompt.",
                epoch + 200,
                display_kind="hidden",
            ),
            _hermes_message("system-uid", "system", "System scaffolding.", epoch + 300),
            _hermes_message("final-uid", "assistant", "The final visible response.", epoch + 5),
        ],
    )
    source, raw = _write_hermes_export(tmp_path / "timestamp-export.jsonl", [session])

    [candidate] = core._hermes_candidates_from_export(source, profile="research")
    assert candidate["start"] == datetime.fromtimestamp(epoch - 2, timezone.utc).isoformat()
    assert candidate["end"] == datetime.fromtimestamp(epoch + 5, timezone.utc).isoformat()
    assert candidate["title"] == "The compacted prompt predates the session clock."
    assert candidate["profile_name"] == "research"

    parsed = core.parse_session_file(candidate["source_jsonl"])
    lines = parsed["loglines"]
    text = [core.extract_text_from_content(line["message"]["content"]) for line in lines]
    assert text == [
        "The compacted prompt predates the session clock.",
        "A compact summary remains eligible.",
        "The final visible response.",
    ]
    assert lines[1]["isCompactSummary"] is True
    assert all(line["timestamp"].endswith("+00:00") for line in lines)

    raw_path = Path(candidate["raw_export_paths"][0])
    logical_path = Path(candidate["source_jsonl"])
    assert raw_path.read_bytes() == raw
    assert stat.S_IMODE(export_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(raw_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(logical_path.stat().st_mode) == 0o600


def test_hermes_exports_split_compression_branches_and_exclude_delegates(tmp_path, monkeypatch):
    monkeypatch.setenv("CTX_HERMES_EXPORTS_DIR", str(tmp_path / "exports"))
    epoch = 1767225600.0
    root = _hermes_session(
        "root",
        started_at=epoch,
        ended_at=epoch + 1,
        end_reason="compression",
        messages=[_hermes_message("copied-uid", "user", "Keep this prompt once.", epoch)],
    )
    compressed = _hermes_session(
        "compressed",
        parent="root",
        started_at=epoch + 2,
        ended_at=epoch + 4,
        model_config={"_branched_from": "an-older-ancestor"},
        messages=[
            _hermes_message("copied-uid", "user", "Keep this prompt once.", epoch),
            _hermes_message("continued-uid", "assistant", "Continue the same logical work.", epoch + 3),
        ],
    )
    branch = _hermes_session(
        "branch",
        parent="root",
        started_at=epoch + 5,
        ended_at=epoch + 6,
        model_config={"_branched_from": "root"},
    )
    reset = _hermes_session(
        "reset",
        parent="root",
        started_at=epoch + 7,
        ended_at=epoch + 8,
        model_config={"_reset_from": "root"},
    )
    delegated = _hermes_session(
        "delegate",
        parent="root",
        source="tool",
        started_at=epoch + 9,
        ended_at=epoch + 10,
        end_reason="compression",
        model_config={"_delegate_from": "root"},
    )
    delegated_child = _hermes_session(
        "delegate-child",
        parent="delegate",
        started_at=epoch + 11,
        ended_at=epoch + 12,
        model_config={"_delegate_from": "root"},
    )
    hidden = _hermes_session("hidden-session", started_at=epoch, ended_at=epoch + 1, hidden=1)
    source, _ = _write_hermes_export(
        tmp_path / "lineage-export.jsonl",
        [compressed, branch, delegated_child, reset, hidden, root, delegated],
    )

    candidates = core._hermes_candidates_from_export(source)
    by_root = {candidate["session_id"]: candidate for candidate in candidates}
    assert set(by_root) == {"root", "branch", "reset"}
    assert by_root["root"]["physical_session_ids"] == ["root", "compressed"]
    assert by_root["branch"]["physical_session_ids"] == ["branch"]
    assert by_root["reset"]["physical_session_ids"] == ["reset"]

    parsed = core.parse_session_file(by_root["root"]["source_jsonl"])
    text = [core.extract_text_from_content(line["message"]["content"]) for line in parsed["loglines"]]
    assert text == ["Keep this prompt once.", "Continue the same logical work."]
    with pytest.raises(ValueError, match="unrelated sessions"):
        core.parse_session_file(source)


def test_hermes_offline_export_rejects_missing_ancestry_and_profile_mismatch(tmp_path, monkeypatch):
    monkeypatch.setenv("CTX_HERMES_EXPORTS_DIR", str(tmp_path / "exports"))
    orphan, _ = _write_hermes_export(
        tmp_path / "orphan.jsonl",
        [_hermes_session("child", parent="missing-parent")],
    )
    with pytest.raises(ValueError, match="missing ancestor"):
        core._hermes_candidates_from_export(orphan)

    other_profile, _ = _write_hermes_export(
        tmp_path / "other-profile.jsonl",
        [_hermes_session("other", profile="another-profile")],
    )
    parsed = core.parse_session_file(other_profile)
    assert parsed["source_format"] == "hermes_jsonl"
    assert core._tool_display_name_from_source_format(parsed["source_format"]) == "Hermes"
    with pytest.raises(ValueError, match="not a claude session log"):
        core._native_session_candidate_from_source(tool="claude", source_jsonl=other_profile)
    with pytest.raises(ValueError, match="profile mismatch"):
        core._hermes_candidates_from_export(other_profile, profile="research")


def test_hermes_discovery_filters_runtime_profile_and_hydrates_parents(tmp_path, monkeypatch):
    monkeypatch.setenv("CTX_HERMES_EXPORTS_DIR", str(tmp_path / "exports"))
    epoch = 1767225600.0
    root = _hermes_session(
        "native-root",
        started_at=epoch,
        ended_at=epoch + 3,
        end_reason="compression",
        transport_profile="root-transport",
        messages=[_hermes_message("root-prompt", "user", "Hydrate the root.", epoch + 1)],
    )
    child = _hermes_session(
        "native-child",
        parent="native-root",
        started_at=epoch + 4,
        transport_profile="child-transport",
        ended_at=None,
        messages=[_hermes_message("child-response", "assistant", "New child response.", epoch + 12)],
    )
    unrelated_profile = _hermes_session("filtered-out", profile="other-runtime-profile")
    parent_output = json.dumps(root) + "\n"
    initial_output = json.dumps(child) + "\n" + json.dumps(unrelated_profile) + "\n"
    calls_path = tmp_path / "calls.jsonl"
    body = f"""
import json
import sys
with open({str(calls_path)!r}, "a", encoding="utf-8") as calls:
    print(json.dumps(sys.argv[1:]), file=calls)
sys.stdout.write({parent_output!r} if "--session-id" in sys.argv else {initial_output!r})
"""
    monkeypatch.setenv("CTX_HERMES_CMD", str(_fake_hermes(tmp_path, body)))

    since = datetime.fromtimestamp(epoch + 10, timezone.utc)
    until = datetime.fromtimestamp(epoch + 30, timezone.utc)
    [candidate] = core._discover_native_sessions(
        tools=("hermes",),
        since=since,
        until=until,
        hermes_profile="research",
    )
    assert candidate["profile_name"] == "research"
    assert candidate["session_id"] == "native-root"
    assert candidate["physical_session_ids"] == ["native-root", "native-child"]
    assert candidate["source"]["provenance"]["transport_profiles"] == [
        "child-transport",
        "root-transport",
    ]
    assert len(candidate["raw_export_paths"]) == 2

    calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
    initial_args, parent_args = calls
    assert initial_args[initial_args.index("--profile") + 1] == "research"
    assert "--newer-than" in initial_args
    assert "--redact" in initial_args
    assert "--session-id" not in initial_args
    assert parent_args[parent_args.index("--session-id") + 1] == "native-root"
    assert "--newer-than" not in parent_args
    assert "--redact" in parent_args


def test_hermes_project_binding_cannot_override_recorded_metadata(tmp_path, monkeypatch):
    roots = {
        "/native-cwd": "/repo-a",
        "/native-git-root": "/repo-b",
        "/operator-binding": "/repo-c",
        "/valid-binding": "/repo-bound",
    }
    monkeypatch.setattr(core, "_git_toplevel_from_path_hint", lambda value: roots.get(value))
    candidate = {
        "tool": "hermes",
        "source_jsonl": str(tmp_path / "candidate.jsonl"),
        "session_id": "root",
        "profile_name": "research",
        "start": "2026-01-01T00:00:00+00:00",
        "end": "2026-01-01T00:01:00+00:00",
        "cwd": "/native-cwd",
        "git_repo_root": "/native-git-root",
        "hermes_project_root": "/operator-binding",
        "hermes_project_evidence": [{"session_id": "root", "cwd": "/native-cwd", "git_repo_root": "/native-git-root"}],
    }

    resolution = core._resolve_native_session_project(candidate)
    assert resolution["project_root"] is None
    assert resolution["evidence"]["cwd"] == "/native-cwd"
    assert resolution["evidence"]["git_repo_root"] == "/native-git-root"
    assert resolution["evidence"]["conflicts"]

    bound = core._resolve_native_session_project(
        {
            **candidate,
            "cwd": None,
            "git_repo_root": None,
            "hermes_project_evidence": [{"session_id": "root", "cwd": None, "git_repo_root": None}],
            "hermes_project_root": "/valid-binding",
        }
    )
    assert bound["project_root"] == "/repo-bound"

    unresolved_native = core._resolve_native_session_project(
        {
            **candidate,
            "cwd": "/unresolvable-native-cwd",
            "git_repo_root": None,
            "hermes_project_evidence": [
                {"session_id": "root", "cwd": "/unresolvable-native-cwd", "git_repo_root": None}
            ],
            "hermes_project_root": "/valid-binding",
        }
    )
    assert unresolved_native["project_root"] is None
    assert unresolved_native["evidence"]["conflicts"]


def test_hermes_reexports_upsert_by_profile_and_logical_root(tmp_path, monkeypatch):
    monkeypatch.setenv("CTX_HERMES_EXPORTS_DIR", str(tmp_path / "exports"))
    monkeypatch.setenv("CTX_CHANGELOG_PROGRESS", "0")
    _install_synthetic_evaluator(monkeypatch)
    epoch = 1767225600.0
    first, _ = _write_hermes_export(
        tmp_path / "first-export.jsonl",
        [
            _hermes_session(
                "stable-root",
                started_at=epoch,
                messages=[
                    _hermes_message("prompt", "user", "Add one synthetic helper.", epoch + 1),
                    _hermes_message("answer", "assistant", "The helper is ready.", epoch + 2),
                ],
            )
        ],
    )
    second, _ = _write_hermes_export(
        tmp_path / "resumed-export.jsonl",
        [
            _hermes_session(
                "stable-root",
                started_at=epoch,
                ended_at=epoch + 4,
                messages=[
                    _hermes_message("prompt", "user", "Add one synthetic helper.", epoch + 1),
                    _hermes_message("answer", "assistant", "The helper is ready.", epoch + 2),
                    _hermes_message("resume", "assistant", "The resumed session updated the work.", epoch + 4),
                ],
                transport_profile="alternate-transport",
            )
        ],
    )
    [first_candidate] = core._hermes_candidates_from_export(first, profile="research")
    [second_candidate] = core._hermes_candidates_from_export(second, profile="research")
    assert first_candidate["source_jsonl"] != second_candidate["source_jsonl"]

    project_root = tmp_path / "repo"
    project_root.mkdir()
    results = []
    for index, candidate in enumerate((first_candidate, second_candidate), start=1):
        results.append(
            core._generate_and_append_changelog_entry(
                tool="hermes",
                label=candidate["title"],
                cwd=str(project_root),
                project_root=project_root,
                session_dir=tmp_path / f"session-{index}",
                start=candidate["start"],
                end=candidate["end"],
                source_jsonl=Path(candidate["source_jsonl"]),
                source_match_json=None,
                actor="synthetic-actor",
                evaluator="codex",
                transcript_output_dir=None,
                transcript_index_html=None,
            )
        )

    assert results[0][0] is True and results[0][2] == "appended"
    assert results[1][0] is True and results[1][2] == "updated"
    assert results[0][1] == results[1][1]
    entries_path, _ = core._changelog_paths(
        changelog_dir=project_root / ".changelog",
        actor="synthetic-actor",
    )
    entries = [json.loads(line) for line in entries_path.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["run_id"] == results[0][1]
    assert entry["tool"] == "hermes"
    assert entry["source"]["identity"]["profile_name"] == "research"
    assert entry["source"]["identity"]["session_id"] == "stable-root"
    assert entry["source"]["provenance"]["physical_session_ids"] == ["stable-root"]
    assert entry["source"]["provenance"]["transport_profile"] == "alternate-transport"
    assert entry["tool"] in core._CHANGELOG_ENTRY_SCHEMA["properties"]["tool"]["enum"]
    assert core._load_changelog_entries(entries_path, tool="hermes") == entries
    assert core._load_changelog_entries(entries_path, tool="codex") == []
    assert entry["created_at"] == first_candidate["start"]
    assert entry["transcript"]["source_jsonl"] == second_candidate["source_jsonl"]


def test_hermes_native_export_boundary_reports_missing_failed_and_malformed_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("CTX_HERMES_EXPORTS_DIR", str(tmp_path / "exports"))
    since = datetime(2026, 1, 1, tzinfo=timezone.utc)
    until = datetime(2026, 1, 2, tzinfo=timezone.utc)

    monkeypatch.setenv("CTX_HERMES_CMD", str(tmp_path / "missing-hermes"))
    with pytest.raises(ValueError, match="Could not run Hermes native export"):
        core._discover_native_sessions(tools=("hermes",), since=since, until=until)

    failing = _fake_hermes(
        tmp_path,
        "import sys\nsys.stderr.write('synthetic native-export failure')\nsys.exit(23)\n",
    )
    monkeypatch.setenv("CTX_HERMES_CMD", str(failing))
    with pytest.raises(ValueError, match="exit status 23.*synthetic native-export failure"):
        core._discover_native_sessions(tools=("hermes",), since=since, until=until)

    malformed = _fake_hermes(
        tmp_path,
        "import sys\nsys.stdout.write('not-json\\\\n')\n",
    )
    monkeypatch.setenv("CTX_HERMES_CMD", str(malformed))
    with pytest.raises(ValueError, match="Malformed Hermes JSONL"):
        core._discover_native_sessions(tools=("hermes",), since=since, until=until)
