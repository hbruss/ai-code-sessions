# Native Session Changelog Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `ais changelog sync` so recent native Codex and Claude sessions can be changelogged directly, without requiring `ais ctx`, while keeping repo targeting safe and idempotent.

**Architecture:** Reuse the existing changelog digest/evaluator pipeline, but add a native-session discovery layer, a repo-resolution/confidence layer, and a canonical session identity that does not depend on wrapper-managed session directories. Keep transcript export decoupled; sync-generated entries will still carry a compatible `transcript` object, but only `source_jsonl` remains mandatory.

**Tech Stack:** Python 3.11, Click, questionary, pytest, Ruff

**Git note:** Do not run `git add`, `git commit`, or any other git write command unless the user explicitly authorizes it in the current session.

---

### Task 1: Add canonical native-session identity and changelog schema compatibility

**Files:**
- Modify: `src/ai_code_sessions/core.py`
- Modify: `tests/test_changelog_validation.py`
- Create: `tests/test_changelog_sync.py`

- [x] **Step 1: Write failing tests for canonical session identity and transcript compatibility**

Add tests that lock in:

```python
def test_native_session_identity_uses_tool_session_and_time_bounds(tmp_path):
    # write a minimal native Codex rollout
    path = tmp_path / "rollout-abc.jsonl"
    identity = core._canonical_session_identity_for_source(
        tool="codex",
        source_jsonl=path,
        start="2026-01-01T00:00:00+00:00",
        end="2026-01-01T00:05:00+00:00",
    )
    assert identity["tool"] == "codex"
    assert identity["native_source_path"] == str(path.resolve())


def test_changelog_schema_allows_sync_entries_without_html_paths():
    entry = {
        "schema_version": core.CHANGELOG_ENTRY_SCHEMA_VERSION,
        "run_id": "run-1",
        "created_at": "2026-01-01T00:00:00+00:00",
        "tool": "codex",
        "actor": "alice",
        "project": "demo",
        "project_root": "/tmp/demo",
        "label": "sync run",
        "start": "2026-01-01T00:00:00+00:00",
        "end": "2026-01-01T00:05:00+00:00",
        "session_dir": "/tmp/demo/.codex/sync/native-rollout-abc",
        "continuation_of_run_id": None,
        "transcript": {
            "output_dir": None,
            "index_html": None,
            "source_jsonl": "/tmp/demo/native/rollout-abc.jsonl",
            "source_match_json": None,
        },
        "summary": "test summary",
        "bullets": ["test bullet"],
        "tags": [],
        "touched_files": {"created": [], "modified": [], "deleted": [], "moved": []},
        "tests": [],
        "commits": [],
        "notes": None,
    }
    validate(instance=entry, schema=core._CHANGELOG_ENTRY_SCHEMA)
```

Run:

```bash
uv run --group dev pytest tests/test_changelog_sync.py tests/test_changelog_validation.py -q
```

Expected: new tests fail because the identity helper does not exist yet and the schema still requires non-null HTML transcript paths.

- [x] **Step 2: Implement canonical identity helpers and relax transcript schema nullability**

Add focused helpers in `src/ai_code_sessions/core.py`:

```python
def _canonical_session_identity_for_source(*, tool: str, source_jsonl: Path, start: str, end: str) -> dict:
    ...


def _entry_session_identity(entry: dict) -> dict | None:
    ...
```

Update the changelog entry schema so:

```python
"transcript": {
    "type": "object",
    "required": ["output_dir", "index_html", "source_jsonl", "source_match_json"],
    "properties": {
        "output_dir": {"type": ["string", "null"]},
        "index_html": {"type": ["string", "null"]},
        "source_jsonl": {"type": "string", "minLength": 1},
        "source_match_json": {"type": ["string", "null"]},
    },
}
```

Also add additive source metadata to new entries, for example:

```python
"source": {
    "identity": canonical_identity,
    "kind": "native_session",
}
```

- [x] **Step 3: Run targeted tests**

Run:

```bash
uv run --group dev pytest tests/test_changelog_sync.py tests/test_changelog_validation.py -q
```

Expected: pass.

### Task 2: Add native session discovery and repo-resolution helpers

**Files:**
- Modify: `src/ai_code_sessions/core.py`
- Modify: `tests/test_changelog_sync.py`

- [x] **Step 1: Write failing tests for discovery windows and repo confidence**

Add tests that lock in:

```python
def test_discover_native_sessions_defaults_to_recent_window(tmp_path, monkeypatch):
    since_dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
    until_dt = datetime(2026, 1, 3, tzinfo=timezone.utc)
    sessions = core._discover_native_sessions(
        tools=("codex",),
        since=since_dt,
        until=until_dt,
    )
    assert sessions


def test_resolve_project_root_high_confidence_from_git_toplevel(tmp_path, monkeypatch):
    candidate = {
        "tool": "codex",
        "cwd": str(tmp_path / "repo"),
        "source_jsonl": tmp_path / "rollout.jsonl",
        "start": "2026-01-01T00:00:00+00:00",
        "end": "2026-01-01T00:05:00+00:00",
    }
    resolution = core._resolve_native_session_project(candidate)
    assert resolution["confidence"] == "high"


def test_resolve_project_root_low_confidence_without_repo_evidence(tmp_path):
    candidate = {
        "tool": "claude",
        "cwd": None,
        "source_jsonl": tmp_path / "session.jsonl",
        "start": "2026-01-01T00:00:00+00:00",
        "end": "2026-01-01T00:05:00+00:00",
    }
    resolution = core._resolve_native_session_project(candidate)
    assert resolution["confidence"] == "low"
```

Run:

```bash
uv run --group dev pytest tests/test_changelog_sync.py -q
```

Expected: fail because discovery/resolution helpers do not exist yet.

- [x] **Step 2: Implement native discovery and repo-resolution helpers**

Add focused helpers in `src/ai_code_sessions/core.py` that reuse existing parsing/time helpers:

```python
def _discover_native_sessions(*, tools: tuple[str, ...], since: datetime, until: datetime) -> list[dict]:
    ...


def _discover_native_codex_sessions(*, since: datetime, until: datetime) -> list[dict]:
    ...


def _discover_native_claude_sessions(*, since: datetime, until: datetime) -> list[dict]:
    ...


def _resolve_native_session_project(candidate: dict) -> dict:
    ...
```

Expected behavior:

- use `_user_codex_sessions_dir()`, `_codex_rollout_session_times()`, and `_claude_session_times()`
- keep only sessions overlapping the time window
- sort newest-first by end timestamp
- classify confidence as `high`, `medium`, or `low`
- include an evidence bundle suitable for `--dry-run` output and interactive prompts

- [x] **Step 3: Run targeted tests**

Run:

```bash
uv run --group dev pytest tests/test_changelog_sync.py -q
```

Expected: pass.

### Task 3: Implement duplicate detection and sync-generated changelog entries

**Files:**
- Modify: `src/ai_code_sessions/core.py`
- Modify: `tests/test_changelog_evaluator_errors.py`
- Modify: `tests/test_cli_changelog.py`
- Modify: `tests/test_changelog_created_at.py`

- [x] **Step 1: Write failing tests for duplicate detection across native sync and prior ctx entries**

Add tests that lock in:

```python
def test_sync_duplicate_detection_matches_existing_ctx_entry(tmp_path):
    existing_entry = {
        "transcript": {"source_jsonl": str(tmp_path / "copied-rollout.jsonl")},
        "start": "2026-01-01T00:00:00+00:00",
        "end": "2026-01-01T00:05:00+00:00",
        "tool": "codex",
    }
    assert core._entry_session_identity(existing_entry) is not None


def test_generate_and_append_changelog_entry_accepts_sync_transcript_fields(tmp_path, monkeypatch):
    appended, run_id, status = core._generate_and_append_changelog_entry(
        tool="codex",
        label="sync run",
        cwd=str(tmp_path),
        project_root=tmp_path,
        session_dir=tmp_path / ".codex" / "sync" / "native-rollout-abc",
        start="2026-01-01T00:00:00+00:00",
        end="2026-01-01T00:05:00+00:00",
        source_jsonl=tmp_path / "rollout-abc.jsonl",
        source_match_json=None,
        transcript_output_dir=None,
        transcript_index_html=None,
    )
    assert appended is True
```

Run:

```bash
uv run --group dev pytest tests/test_changelog_evaluator_errors.py tests/test_changelog_created_at.py tests/test_cli_changelog.py -q
```

Expected: fail because the append path still assumes ctx-managed transcript paths and duplicate detection still depends on wrapper-specific run identity.

- [x] **Step 2: Update append and duplicate logic for sync-generated entries**

Extend `src/ai_code_sessions/core.py` so `_generate_and_append_changelog_entry(...)` can accept optional transcript-path arguments and always record source identity. Use a stable duplicate check that works for both:

- existing ctx/backfill entries by re-reading `transcript.source_jsonl`
- new sync-generated entries via recorded canonical source identity

The updated entry shape should look like:

```python
entry = {
    ...,
    "session_dir": str(session_dir_abs),
    "transcript": {
        "output_dir": transcript_output_dir,
        "index_html": transcript_index_html,
        "source_jsonl": str(source_jsonl),
        "source_match_json": transcript_source_match_json,
    },
    "source": {
        "kind": "native_session",
        "identity": canonical_identity,
    },
}
```

- [x] **Step 3: Run targeted tests**

Run:

```bash
uv run --group dev pytest tests/test_changelog_evaluator_errors.py tests/test_changelog_created_at.py tests/test_cli_changelog.py -q
```

Expected: pass.

### Task 4: Add `ais changelog sync` CLI, prompting, and compatibility updates

**Files:**
- Modify: `src/ai_code_sessions/cli.py`
- Modify: `src/ai_code_sessions/core.py`
- Modify: `tests/test_cli_changelog.py`
- Modify: `tests/test_cli_setup_web_export_backfill.py`

- [x] **Step 1: Write failing CLI tests for sync behavior**

Add tests that lock in:

```python
def test_changelog_sync_defaults_to_48_hours(monkeypatch, tmp_path):
    result = CliRunner().invoke(cli, ["changelog", "sync", "--project-root", str(tmp_path), "--dry-run"])
    assert result.exit_code == 0


def test_changelog_sync_prompts_for_medium_confidence(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_module.questionary, "select", lambda *a, **k: _Answer(str(tmp_path / "repo")))
    result = CliRunner().invoke(cli, ["changelog", "sync", "--codex"])
    assert result.exit_code == 0


def test_changelog_sync_skips_low_confidence_sessions(monkeypatch, tmp_path):
    result = CliRunner().invoke(cli, ["changelog", "sync", "--claude", "--dry-run"])
    assert "unresolved" in result.output.lower()
```

Run:

```bash
uv run --group dev pytest tests/test_cli_changelog.py tests/test_cli_setup_web_export_backfill.py -q
```

Expected: fail because the `sync` command does not exist yet.

- [x] **Step 2: Implement the CLI command and prompt flow**

Add a new subcommand in `src/ai_code_sessions/cli.py`:

```python
@changelog_cli.command("sync")
@click.option("--codex", "tool_codex", is_flag=True)
@click.option("--claude", "tool_claude", is_flag=True)
@click.option("--all", "tool_all", is_flag=True)
@click.option("--since", default="48 hours ago")
@click.option("--until")
@click.option("--limit", type=int)
@click.option("--project-root")
@click.option("--dry-run", is_flag=True)
def changelog_sync_cmd(...):
    ...
```

Implementation requirements:

- resolve the requested tools
- parse `--since` and `--until`
- discover native candidates
- resolve repo confidence
- prompt with `questionary.select()` on medium confidence
- skip low confidence
- call the append pipeline with sync-compatible transcript metadata
- print clear dry-run and processed/skip/unresolved summaries

- [x] **Step 3: Update compatibility paths that read changelog entries**

Adjust lint/refresh paths so they continue to work when:

- `transcript.index_html` is `null`
- `transcript.output_dir` is `null`
- `transcript.source_match_json` is `null`

They should continue to require a readable `transcript.source_jsonl`.

- [x] **Step 4: Run targeted tests**

Run:

```bash
uv run --group dev pytest tests/test_changelog_sync.py tests/test_cli_changelog.py tests/test_cli_setup_web_export_backfill.py tests/test_changelog_evaluator_errors.py tests/test_changelog_created_at.py -q
```

Expected: pass.

### Task 5: Update docs and run verification

**Files:**
- Modify: `README.md`
- Modify: `docs/changelog.md`
- Modify: `docs/cli.md`
- Modify: `docs/ctx.md`

- [x] **Step 1: Update docs for the new primary workflow**

Add explicit usage examples such as:

```bash
ais changelog sync --codex
ais changelog sync --all --since "7 days ago"
ais changelog sync --claude --dry-run
```

Document that:

- sync is the normal post-session workflow
- `ais ctx` is now optional convenience
- transcript export remains separate
- ambiguous repo matches prompt interactively

- [x] **Step 2: Run lint, format, and targeted tests**

Run:

```bash
uv run --group dev ruff check --fix src/ai_code_sessions/core.py src/ai_code_sessions/cli.py tests/test_changelog_sync.py tests/test_cli_changelog.py tests/test_cli_setup_web_export_backfill.py tests/test_changelog_validation.py tests/test_changelog_evaluator_errors.py tests/test_changelog_created_at.py README.md docs/changelog.md docs/cli.md docs/ctx.md
uv run --group dev ruff format src/ai_code_sessions/core.py src/ai_code_sessions/cli.py tests/test_changelog_sync.py tests/test_cli_changelog.py tests/test_cli_setup_web_export_backfill.py tests/test_changelog_validation.py tests/test_changelog_evaluator_errors.py tests/test_changelog_created_at.py
uv run --group dev pytest tests/test_changelog_sync.py tests/test_cli_changelog.py tests/test_cli_setup_web_export_backfill.py tests/test_changelog_validation.py tests/test_changelog_evaluator_errors.py tests/test_changelog_created_at.py -q
```

Expected: clean Ruff output and passing targeted tests.

- [x] **Step 3: Run a broader regression check**

Run:

```bash
uv run --group dev pytest -q
```

Expected: full suite passes. If the full suite is too slow or reveals unrelated failures, record that explicitly before finishing.
