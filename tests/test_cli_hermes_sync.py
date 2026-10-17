"""End-to-end changelog CLI behavior for Hermes native exports."""

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

import ai_code_sessions as package

core_module = importlib.import_module("ai_code_sessions.core")
cli_module = importlib.import_module("ai_code_sessions.cli")
cli = cli_module.cli

_BASE_TIME = 1767225600


def _message(uid: str, role: str, content: str, offset: int) -> dict:
    return {
        "id": offset,
        "message_uid": uid,
        "role": role,
        "content": content,
        "timestamp": _BASE_TIME + offset,
        "active": 1,
        "compacted": 0,
    }


def _session(
    session_id: str,
    *,
    profile: str = "work-profile",
    cwd: str | None = None,
    git_repo_root: str | None = None,
    started_at: int = _BASE_TIME,
    ended_at: int | None = _BASE_TIME + 10,
    messages: list[dict] | None = None,
    parent_session_id: str | None = None,
    end_reason: str | None = None,
    model_config: dict | None = None,
) -> dict:
    return {
        "id": session_id,
        "profile_name": profile,
        "transport_profile": "default",
        "source": "discord",
        "started_at": float(started_at),
        "ended_at": float(ended_at) if ended_at is not None else None,
        "cwd": cwd,
        "git_repo_root": git_repo_root,
        "parent_session_id": parent_session_id,
        "end_reason": end_reason,
        "model_config": model_config or {},
        "messages": messages
        if messages is not None
        else [
            _message(f"{session_id}-user", "user", f"Change for {session_id}.", 1),
            _message(f"{session_id}-assistant", "assistant", f"Completed {session_id}.", 2),
        ],
    }


def _write_export(path: Path, *sessions: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(session) + "\n" for session in sessions), encoding="utf-8")
    return path


def _read_entries(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _isolate_hermes_exports(monkeypatch, tmp_path: Path) -> Path:
    export_dir = tmp_path / "hermes-exports"
    monkeypatch.setenv("CTX_HERMES_EXPORTS_DIR", str(export_dir))
    monkeypatch.setenv("CTX_CHANGELOG_MODEL", "")
    monkeypatch.setenv("AI_CODE_SESSIONS_CHANGELOG_MODEL", "")
    return export_dir


def _stub_external_evaluator(monkeypatch):
    calls = []

    def evaluate(**_kwargs):
        calls.append(None)
        return {
            "summary": f"Hermes evaluation {len(calls)}",
            "bullets": [f"Recorded Hermes change {len(calls)}."],
            "tags": ["hermes"],
            "notes": None,
        }

    monkeypatch.setattr(package, "_run_codex_changelog_evaluator", evaluate)
    return calls


def _mock_git_roots(monkeypatch, *roots: Path) -> None:
    resolved_roots = tuple(root.resolve() for root in roots)

    def git_toplevel(path: Path) -> Path | None:
        resolved_path = Path(path).expanduser().resolve()
        matches = [root for root in resolved_roots if resolved_path == root or resolved_path.is_relative_to(root)]
        return max(matches, key=lambda root: len(root.parts)) if matches else None

    monkeypatch.setattr(cli_module, "_git_toplevel", git_toplevel)
    monkeypatch.setattr(core_module, "_git_toplevel", git_toplevel)


def _hermes_export_command(monkeypatch, tmp_path: Path, session: dict | None = None) -> Path:
    """A process-boundary fixture, not a replacement for the app's discovery."""
    marker = tmp_path / "hermes-invocations"
    executable = tmp_path / "hermes"
    program = (
        f"#!{sys.executable}\n"
        "import pathlib, sys\n"
        f"with pathlib.Path({str(marker)!r}).open('a') as log: log.write('export\\n')\n"
    )
    if session is None:
        program += "sys.stderr.write('native export refused\\n')\nsys.exit(3)\n"
    else:
        program += f"sys.stdout.write({(json.dumps(session) + chr(10))!r})\n"
    executable.write_text(program, encoding="utf-8")
    executable.chmod(0o700)
    monkeypatch.setenv("CTX_HERMES_CMD", str(executable))
    monkeypatch.setenv("AI_CODE_SESSIONS_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "home" / ".codex"))
    monkeypatch.setenv("CTX_OMP_SESSIONS_DIR", str(tmp_path / "omp"))
    return marker


def _sync_args(
    source_jsonl: Path,
    repo_root: Path,
    *extra: str,
    binding_root: Path | None = None,
) -> list[str]:
    return [
        "changelog",
        "sync",
        "--hermes",
        "--hermes-profile",
        "work-profile",
        "--hermes-project-root",
        str(binding_root or repo_root),
        "--project-root",
        str(repo_root),
        "--source-jsonl",
        str(source_jsonl),
        "--actor",
        "cli-actor",
        "--evaluator",
        "codex",
        *extra,
    ]


def test_hermes_source_profile_binding_persists_and_query_filters(tmp_path, monkeypatch):
    _isolate_hermes_exports(monkeypatch, tmp_path)
    repo_root = tmp_path / "repo"
    nested_binding = repo_root / "nested"
    other_root = tmp_path / "other-repo"
    nested_binding.mkdir(parents=True)
    other_root.mkdir()
    _mock_git_roots(monkeypatch, repo_root, other_root)
    monkeypatch.setenv("CTX_ACTOR", "environment-actor")
    evaluator_calls = _stub_external_evaluator(monkeypatch)

    source_jsonl = _write_export(
        tmp_path / "source" / "profile-session.jsonl",
        _session("profile-session", profile="work-profile"),
    )
    args = _sync_args(source_jsonl, repo_root, binding_root=nested_binding)
    args[args.index("--hermes-profile") + 1] = "  WoRk-ProFile  "

    # Existing --project-root remains a filter; the Hermes binding is not a way to
    # route a candidate into a different requested target.
    scope_index = args.index("--project-root")
    mismatched_args = [
        *args[:scope_index],
        "--project-root",
        str(other_root),
        *args[scope_index + 2 :],
    ]
    mismatched_target = CliRunner().invoke(cli, mismatched_args)
    assert mismatched_target.exit_code == 0, mismatched_target.output
    assert "skipped=1" in mismatched_target.output
    entries_path = repo_root / ".changelog" / "cli-actor" / "entries.jsonl"
    assert _read_entries(entries_path) == []
    assert evaluator_calls == []

    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert "processed=1" in result.output
    entries = _read_entries(entries_path)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["tool"] == "hermes"
    assert entry["actor"] == "cli-actor"
    assert entry["project_root"] == str(repo_root.resolve())
    assert entry["summary"] == "Hermes evaluation 1"
    assert entry["source"]["identity"]["profile_name"] == "work-profile"
    assert entry["source"]["identity"]["session_id"] == "profile-session"
    assert evaluator_calls == [None]

    other_actor_entries = repo_root / ".changelog" / "other-actor" / "entries.jsonl"
    other_actor_entries.parent.mkdir(parents=True)
    other_actor_entries.write_text(
        json.dumps(
            {
                "run_id": "legacy-codex-run",
                "created_at": "2026-10-12T12:00:00+00:00",
                "tool": "codex",
                "actor": "other-actor",
                "summary": "A Codex entry must not pass the Hermes filter.",
                "bullets": [],
                "tags": [],
                "start": "2026-10-12T11:00:00+00:00",
                "end": "2026-10-12T11:05:00+00:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    query = CliRunner().invoke(
        cli,
        [
            "changelog",
            "since",
            "2026-01-01",
            "--project-root",
            str(repo_root),
            "--tool",
            "hermes",
            "--format",
            "json",
        ],
    )
    assert query.exit_code == 0, query.output
    queried_entries = json.loads(query.output)
    assert [row["tool"] for row in queried_entries] == ["hermes"]
    assert queried_entries[0]["summary"] == "Hermes evaluation 1"


def test_hermes_explicit_export_without_profile_accepts_native_profile(tmp_path, monkeypatch):
    _isolate_hermes_exports(monkeypatch, tmp_path)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _mock_git_roots(monkeypatch, repo_root)
    _stub_external_evaluator(monkeypatch)
    source_jsonl = _write_export(
        tmp_path / "source.jsonl",
        _session(
            "research-session",
            profile="research",
            cwd=str(repo_root),
            git_repo_root=str(repo_root),
        ),
    )

    result = CliRunner().invoke(
        cli,
        [
            "changelog",
            "sync",
            "--hermes",
            "--source-jsonl",
            str(source_jsonl),
            "--project-root",
            str(repo_root),
            "--actor",
            "profile-optional",
            "--evaluator",
            "codex",
        ],
    )
    assert result.exit_code == 0, result.output
    entries = _read_entries(repo_root / ".changelog" / "profile-optional" / "entries.jsonl")
    assert len(entries) == 1
    assert entries[0]["source"]["identity"]["profile_name"] == "research"
    assert entries[0]["source"]["identity"]["session_id"] == "research-session"


def test_hermes_compaction_resume_and_cross_file_resync_upsert_one_row(tmp_path, monkeypatch):
    _isolate_hermes_exports(monkeypatch, tmp_path)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _mock_git_roots(monkeypatch, repo_root)
    evaluator_calls = _stub_external_evaluator(monkeypatch)
    parent = _session(
        "logical-root",
        started_at=_BASE_TIME,
        ended_at=_BASE_TIME + 20,
        end_reason="compression",
        messages=[
            _message("shared-prompt", "user", "Implement the behavior.", 1),
            _message("root-response", "assistant", "Inspecting the code.", 2),
        ],
    )
    first_export = _write_export(tmp_path / "first" / "export.jsonl", parent)

    first = CliRunner().invoke(cli, _sync_args(first_export, repo_root))
    assert first.exit_code == 0, first.output
    entries_path = repo_root / ".changelog" / "cli-actor" / "entries.jsonl"
    first_entries = _read_entries(entries_path)
    assert len(first_entries) == 1
    original = first_entries[0]

    child_messages = [
        _message("shared-prompt", "user", "Copied prompt in compressed export.", 1),
        _message("child-prompt", "user", "Continue with the compaction-safe change.", 23),
        _message("child-response", "assistant", "The change is complete.", 24),
    ]
    child = _session(
        "physical-child",
        started_at=_BASE_TIME + 21,
        ended_at=_BASE_TIME + 30,
        parent_session_id="logical-root",
        messages=child_messages,
    )
    compacted_export = _write_export(tmp_path / "second" / "export.jsonl", parent, child)
    compacted = CliRunner().invoke(cli, _sync_args(compacted_export, repo_root))
    assert compacted.exit_code == 0, compacted.output
    assert "updated existing run_id=" in compacted.output
    compacted_entries = _read_entries(entries_path)
    assert len(compacted_entries) == 1
    updated = compacted_entries[0]
    assert updated["run_id"] == original["run_id"]
    assert updated["created_at"] == original["created_at"]
    assert updated["summary"] == "Hermes evaluation 2"
    assert updated["source"]["identity"]["session_id"] == "logical-root"
    assert len(evaluator_calls) == 2

    resumed_child = _session(
        "physical-child",
        started_at=_BASE_TIME + 21,
        ended_at=_BASE_TIME + 45,
        parent_session_id="logical-root",
        messages=[
            *child_messages,
            _message("resume-prompt", "user", "Resume and record the final check.", 35),
        ],
    )
    resumed_export = _write_export(tmp_path / "resumed" / "export.jsonl", parent, resumed_child)
    resumed = CliRunner().invoke(cli, _sync_args(resumed_export, repo_root))
    assert resumed.exit_code == 0, resumed.output
    assert "updated existing run_id=" in resumed.output
    resumed_entries = _read_entries(entries_path)
    assert len(resumed_entries) == 1
    assert resumed_entries[0]["run_id"] == original["run_id"]
    assert resumed_entries[0]["created_at"] == original["created_at"]
    assert resumed_entries[0]["summary"] == "Hermes evaluation 3"
    assert len(evaluator_calls) == 3


def test_hermes_binding_refuses_conflicting_native_project_evidence(tmp_path, monkeypatch):
    _isolate_hermes_exports(monkeypatch, tmp_path)
    cwd_repo = tmp_path / "cwd-repo"
    recorded_repo = tmp_path / "recorded-repo"
    binding_repo = tmp_path / "binding-repo"
    for repo in (cwd_repo, recorded_repo, binding_repo):
        repo.mkdir()
    _mock_git_roots(monkeypatch, cwd_repo, recorded_repo, binding_repo)
    evaluator_calls = _stub_external_evaluator(monkeypatch)
    source_jsonl = _write_export(
        tmp_path / "conflicting.jsonl",
        _session(
            "conflicting-session",
            cwd=str(cwd_repo),
            git_repo_root=str(recorded_repo),
        ),
    )

    conflict_args = _sync_args(source_jsonl, binding_repo)
    scope_index = conflict_args.index("--project-root")
    del conflict_args[scope_index : scope_index + 2]
    result = CliRunner().invoke(cli, conflict_args)
    assert result.exit_code == 0, result.output
    assert "unresolved=1" in result.output
    assert _read_entries(binding_repo / ".changelog" / "cli-actor" / "entries.jsonl") == []
    assert evaluator_calls == []


def test_hermes_explicit_source_profile_mismatch_and_export_failure_are_click_errors(tmp_path, monkeypatch):
    export_dir = _isolate_hermes_exports(monkeypatch, tmp_path)
    wrong_profile_export = _write_export(
        tmp_path / "wrong-profile.jsonl",
        _session("other-profile-session", profile="research"),
    )
    monkeypatch.setattr(
        cli_module,
        "_discover_native_sessions",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("explicit export must not fall back to discovery")),
    )

    mismatch = CliRunner().invoke(
        cli,
        [
            "changelog",
            "sync",
            "--hermes",
            "--hermes-profile",
            "work-profile",
            "--source-jsonl",
            str(wrong_profile_export),
        ],
    )
    assert mismatch.exit_code != 0
    assert "Error: --source-jsonl" in mismatch.output
    assert "profile" in mismatch.output.lower()
    assert "Traceback" not in mismatch.output
    assert not export_dir.exists()

    malformed_export = tmp_path / "malformed.jsonl"
    malformed_export.write_text("not JSON\n", encoding="utf-8")
    malformed = CliRunner().invoke(
        cli,
        ["changelog", "sync", "--hermes", "--source-jsonl", str(malformed_export)],
    )
    assert malformed.exit_code != 0
    assert "Error: --source-jsonl" in malformed.output
    assert "Traceback" not in malformed.output
    assert not export_dir.exists()


def test_hermes_native_export_failure_stops_sync_without_entries(tmp_path, monkeypatch):
    _isolate_hermes_exports(monkeypatch, tmp_path)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    marker = _hermes_export_command(monkeypatch, tmp_path)
    result = CliRunner().invoke(
        cli,
        ["changelog", "sync", "--hermes", "--project-root", str(repo_root)],
    )
    assert result.exit_code != 0
    assert "native export refused" in result.output
    assert marker.read_text() == "export\n"
    assert not (repo_root / ".changelog").exists()


def test_hermes_discovery_does_not_bind_another_runtime_profile(tmp_path, monkeypatch):
    _isolate_hermes_exports(monkeypatch, tmp_path)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(repo_root)], check=True)
    evaluator_calls = _stub_external_evaluator(monkeypatch)
    marker = _hermes_export_command(monkeypatch, tmp_path, _session("foreign-session", profile="research"))
    result = CliRunner().invoke(
        cli,
        [
            "changelog",
            "sync",
            "--hermes",
            "--hermes-profile",
            "work-profile",
            "--hermes-project-root",
            str(repo_root),
            "--since",
            "2026-01-01",
            "--until",
            "2026-01-02",
            "--actor",
            "cli-actor",
        ],
    )
    assert result.exit_code == 0, result.output
    assert marker.read_text() == "export\n"
    assert not (repo_root / ".changelog").exists()
    assert evaluator_calls == []


def test_hermes_source_jsonl_keeps_single_source_and_time_window_validation(tmp_path):
    source_jsonl = _write_export(tmp_path / "source.jsonl", _session("window-session"))
    runner = CliRunner()

    multiple_sources = runner.invoke(
        cli,
        [
            "changelog",
            "sync",
            "--hermes",
            "--codex",
            "--source-jsonl",
            str(source_jsonl),
        ],
    )
    assert multiple_sources.exit_code != 0
    assert "requires exactly one of" in multiple_sources.output
    assert "--hermes" in multiple_sources.output

    time_window = runner.invoke(
        cli,
        [
            "changelog",
            "sync",
            "--hermes",
            "--source-jsonl",
            str(source_jsonl),
            "--since",
            "2026-01-01",
        ],
    )
    assert time_window.exit_code != 0
    assert "cannot be combined with --since/--until" in time_window.output


def test_hermes_is_opt_in_without_losing_existing_omp_entries(tmp_path, monkeypatch):
    _isolate_hermes_exports(monkeypatch, tmp_path)
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(repo_root)], check=True)
    marker = _hermes_export_command(
        monkeypatch,
        tmp_path,
        _session("hermes-session", profile="default", cwd=str(repo_root)),
    )
    omp_source = tmp_path / "omp" / "project" / "session.jsonl"
    omp_source.parent.mkdir(parents=True)
    omp_source.write_text(
        json.dumps(
            {
                "type": "session",
                "id": "omp-session",
                "version": 3,
                "cwd": str(repo_root),
                "timestamp": "2026-01-01T00:00:00Z",
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "message",
                "timestamp": "2026-01-01T00:00:01Z",
                "message": {"role": "user", "content": [{"type": "text", "text": "Fix arithmetic."}]},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    evaluator_calls = _stub_external_evaluator(monkeypatch)
    base_args = [
        "changelog",
        "sync",
        "--project-root",
        str(repo_root),
        "--since",
        "2026-01-01",
        "--until",
        "2026-01-02",
        "--actor",
        "selection-test",
        "--evaluator",
        "codex",
    ]
    entries_path = repo_root / ".changelog" / "selection-test" / "entries.jsonl"
    runner = CliRunner()

    for flags in ([], ["--all"]):
        result = runner.invoke(cli, [*base_args, *flags])
        assert result.exit_code == 0, result.output
        assert not marker.exists()
        assert [entry["tool"] for entry in _read_entries(entries_path)] == ["omp"]

    for flags in (["--hermes"], ["--all", "--hermes"]):
        result = runner.invoke(cli, [*base_args, *flags])
        assert result.exit_code == 0, result.output
        assert [entry["tool"] for entry in _read_entries(entries_path)] == ["omp", "hermes"]
    assert marker.read_text() == "export\nexport\n"
    assert len(evaluator_calls) == 2


def test_hermes_profile_and_binding_options_require_valid_selection_and_repo(tmp_path, monkeypatch):
    source_jsonl = _write_export(tmp_path / "source.jsonl", _session("option-session"))
    runner = CliRunner()

    profile_without_source = runner.invoke(
        cli,
        ["changelog", "sync", "--hermes-profile", "work-profile", "--source-jsonl", str(source_jsonl)],
    )
    assert profile_without_source.exit_code != 0
    assert "--hermes-profile" in profile_without_source.output
    assert "require --hermes" in profile_without_source.output

    binding_without_source = runner.invoke(
        cli,
        [
            "changelog",
            "sync",
            "--hermes-profile",
            "work-profile",
            "--hermes-project-root",
            str(tmp_path),
            "--source-jsonl",
            str(source_jsonl),
        ],
    )
    assert binding_without_source.exit_code != 0
    assert "--hermes-project-root" in binding_without_source.output
    assert "require --hermes" in binding_without_source.output

    binding_without_profile = runner.invoke(
        cli,
        ["changelog", "sync", "--hermes", "--hermes-project-root", str(tmp_path), "--source-jsonl", str(source_jsonl)],
    )
    assert binding_without_profile.exit_code != 0
    assert "requires an explicit --hermes-profile" in binding_without_profile.output

    monkeypatch.setattr(cli_module, "_git_toplevel", lambda _path: None)
    binding_outside_git = runner.invoke(
        cli,
        [
            "changelog",
            "sync",
            "--hermes",
            "--hermes-profile",
            "work-profile",
            "--hermes-project-root",
            str(tmp_path),
            "--source-jsonl",
            str(source_jsonl),
        ],
    )
    assert binding_outside_git.exit_code != 0
    assert "must resolve to a Git repository" in binding_outside_git.output


def test_project_filter_cannot_clear_conflicting_hermes_binding(tmp_path, monkeypatch):
    _isolate_hermes_exports(monkeypatch, tmp_path)
    native_repo = tmp_path / "native-repo"
    bound_repo = tmp_path / "bound-repo"
    native_repo.mkdir()
    bound_repo.mkdir()
    _mock_git_roots(monkeypatch, native_repo, bound_repo)
    calls = _stub_external_evaluator(monkeypatch)
    source = _write_export(
        tmp_path / "native.jsonl",
        _session("native-session", cwd=str(native_repo), git_repo_root=str(native_repo)),
    )
    result = CliRunner().invoke(cli, _sync_args(source, native_repo, binding_root=bound_repo))
    assert result.exit_code == 0, result.output
    assert not (native_repo / ".changelog").exists()
    assert not (bound_repo / ".changelog").exists()
    assert calls == []


@pytest.mark.parametrize("directory_exists", [False, True], ids=["missing-directory", "missing-git-metadata"])
def test_deleted_native_repo_is_not_reassigned_to_enclosing_repo(tmp_path, monkeypatch, directory_exists):
    _isolate_hermes_exports(monkeypatch, tmp_path)
    enclosing_repo = tmp_path / "enclosing"
    enclosing_repo.mkdir()
    subprocess.run(["git", "init", "--quiet", str(enclosing_repo)], check=True)
    missing_repo = enclosing_repo / "removed-nested-repo"
    if directory_exists:
        missing_repo.mkdir()
    source = _write_export(
        tmp_path / "deleted-repo.jsonl",
        _session("deleted-repo-session", cwd=str(missing_repo), git_repo_root=str(missing_repo)),
    )
    calls = _stub_external_evaluator(monkeypatch)
    result = CliRunner().invoke(
        cli,
        [
            "changelog",
            "sync",
            "--hermes",
            "--hermes-profile",
            "work-profile",
            "--source-jsonl",
            str(source),
            "--project-root",
            str(enclosing_repo),
            "--actor",
            "cli-actor",
            "--evaluator",
            "codex",
        ],
    )
    assert result.exit_code == 0, result.output
    assert not (enclosing_repo / ".changelog").exists()
    assert calls == []


def test_new_session_sync_survives_relocation_of_prior_session_artifact(tmp_path, monkeypatch):
    _isolate_hermes_exports(monkeypatch, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    _mock_git_roots(monkeypatch, repo)
    _stub_external_evaluator(monkeypatch)
    first_source = _write_export(tmp_path / "first.jsonl", _session("first-session"))
    runner = CliRunner()
    first = runner.invoke(cli, _sync_args(first_source, repo))
    assert first.exit_code == 0, first.output
    entries_path = repo / ".changelog" / "cli-actor" / "entries.jsonl"
    original = json.loads(entries_path.read_text().splitlines()[0])
    Path(original["transcript"]["source_jsonl"]).rename(tmp_path / "retained-evidence.jsonl")

    second_source = _write_export(tmp_path / "second.jsonl", _session("second-session"))
    second = runner.invoke(cli, _sync_args(second_source, repo))
    assert second.exit_code == 0, str(second.exception) or second.output
    entries = [json.loads(line) for line in entries_path.read_text().splitlines()]
    assert [entry["source"]["identity"]["session_id"] for entry in entries] == ["first-session", "second-session"]
    assert entries[0] == original


def test_hermes_explicit_export_limit_bounds_evaluation_and_entries(tmp_path, monkeypatch):
    _isolate_hermes_exports(monkeypatch, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    _mock_git_roots(monkeypatch, repo)
    calls = _stub_external_evaluator(monkeypatch)
    source = _write_export(
        tmp_path / "multiple.jsonl",
        _session("first", cwd=str(repo)),
        _session("second", cwd=str(repo)),
    )

    result = CliRunner().invoke(
        cli,
        [
            "changelog",
            "sync",
            "--hermes",
            "--source-jsonl",
            str(source),
            "--limit",
            "1",
            "--actor",
            "limit-test",
            "--evaluator",
            "codex",
        ],
    )
    assert result.exit_code == 0, result.output
    entries = _read_entries(repo / ".changelog" / "limit-test" / "entries.jsonl")
    assert [entry["source"]["identity"]["session_id"] for entry in entries] == ["first"]
    assert len(calls) == 1


def test_hermes_empty_lineages_skip_evaluation_but_read_only_continuations_sync(tmp_path, monkeypatch):
    export_dir = _isolate_hermes_exports(monkeypatch, tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    _mock_git_roots(monkeypatch, repo)
    calls = _stub_external_evaluator(monkeypatch)
    metadata_only = _session(
        "metadata-only",
        cwd=str(repo),
        messages=[_message("metadata", "session_meta", "Session setup.", 1)],
    )
    empty_root = _session("empty-root", cwd=str(repo), messages=[], end_reason="compression")
    filtered_only = _session(
        "filtered-only",
        cwd=str(repo),
        messages=[
            _message("system", "system", "System instructions.", 1),
            {**_message("hidden", "user", "Hidden scaffolding.", 2), "display_kind": "hidden"},
            {**_message("undone", "assistant", "Undone response.", 3), "active": 0},
        ],
    )
    source = _write_export(tmp_path / "sessions.jsonl", metadata_only, empty_root, filtered_only)
    empty_raw = source.read_bytes()
    args = [
        "changelog",
        "sync",
        "--hermes",
        "--source-jsonl",
        str(source),
        "--actor",
        "empty-check",
        "--evaluator",
        "codex",
        "--limit",
        "1",
    ]
    runner = CliRunner()

    empty = runner.invoke(cli, args)
    assert empty.exit_code == 0, empty.output
    assert calls == []
    assert not (repo / ".changelog").exists()
    assert any(path.read_bytes() == empty_raw for path in export_dir.glob("raw/*.jsonl"))

    read_only_child = _session(
        "read-only-child",
        cwd=str(repo),
        parent_session_id="empty-root",
        started_at=_BASE_TIME + 11,
        ended_at=_BASE_TIME + 20,
        messages=[
            _message("question", "user", "Review the existing design without changing files.", 12),
            _message("discussion", "assistant", "The current design keeps source and evaluator separate.", 13),
        ],
    )
    _write_export(source, metadata_only, empty_root, filtered_only, read_only_child)
    result = runner.invoke(cli, args)
    assert result.exit_code == 0, result.output
    entries = _read_entries(repo / ".changelog" / "empty-check" / "entries.jsonl")
    assert [entry["source"]["identity"]["session_id"] for entry in entries] == ["empty-root"]
    assert entries[0]["source"]["provenance"]["physical_session_ids"] == ["empty-root", "read-only-child"]
    assert len(calls) == 1
