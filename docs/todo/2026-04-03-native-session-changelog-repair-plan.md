# Native Session Changelog Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the unstable native-session dedup model with stable logical session identity, upsert sync-owned rows instead of appending duplicates, and add a conservative repair path for existing duplicate changelog rows.

**Architecture:** Keep one native-session row per logical Codex or Claude session by deriving a stable identity from `session_id` when available and falling back to normalized source path plus normalized start. Normal sync uses repo-global identity lookup and rewrites sync-owned rows in place; cleanup is a separate backup-first maintenance command that only auto-collapses high-confidence duplicate groups.

**Tech Stack:** Python 3.11, Click, pytest, Ruff, JSONL changelog files

---

### Task 1: Replace mutable native identity with stable logical identity

**Files:**
- Modify: `src/ai_code_sessions/core.py`
- Modify: `tests/test_changelog_sync.py`

- [ ] **Step 1: Write failing tests for stable identity keys**

Add tests in `tests/test_changelog_sync.py` that lock in:

```python
def test_session_identity_key_prefers_session_id(tmp_path):
    identity = {
        "tool": "codex",
        "session_id": "sess-123",
        "native_source_path": str((tmp_path / "rollout.jsonl").resolve()),
        "start": "2026-01-01T00:00:00+00:00",
    }
    assert core._session_identity_key(identity) == ("session_id", "codex", "sess-123")


def test_session_identity_key_falls_back_to_path_and_start(tmp_path):
    path = (tmp_path / "rollout.jsonl").resolve()
    identity = {
        "tool": "claude",
        "native_source_path": str(path),
        "start": "2026-01-01T00:00:00+00:00",
    }
    assert core._session_identity_key(identity) == ("path_start", "claude", str(path), "2026-01-01T00:00:00+00:00")


def test_canonical_session_identity_excludes_end_and_keeps_session_id(tmp_path):
    path = tmp_path / "rollout.jsonl"
    identity = core._canonical_session_identity_for_source(
        tool="codex",
        source_jsonl=path,
        start="2026-01-01T00:00:00Z",
        end="2026-01-01T00:05:00Z",
        session_id="sess-123",
    )
    assert identity == {
        "tool": "codex",
        "session_id": "sess-123",
        "native_source_path": str(path.resolve()),
        "start": "2026-01-01T00:00:00+00:00",
    }
```

Run:

```bash
uv run --group dev pytest tests/test_changelog_sync.py -q
```

Expected: fail because the current canonical identity and key helpers still depend on `end`.

- [ ] **Step 2: Implement stable identity helpers in `src/ai_code_sessions/core.py`**

Update the identity helpers so they follow this shape:

```python
def _canonical_session_identity_for_source(
    *, tool: str, source_jsonl: Path, start: str, end: str, session_id: str | None = None
) -> dict:
    return {
        "tool": normalized_tool,
        "session_id": normalized_session_id_or_none,
        "native_source_path": str(Path(source_jsonl).resolve()),
        "start": canonical_start,
    }


def _session_identity_key(identity: dict | None) -> tuple[str, ...] | None:
    if isinstance(identity, dict) and isinstance(identity.get("session_id"), str) and identity["session_id"].strip():
        return ("session_id", tool, session_id)
    return ("path_start", tool, native_source_path, start)
```

Also update transcript-derived identity reconstruction so current native transcript parsing passes `session_id` through whenever it is discoverable from the source log.

- [ ] **Step 3: Run the targeted test file**

Run:

```bash
uv run --group dev pytest tests/test_changelog_sync.py -q
```

Expected: pass for the new stable identity assertions.

### Task 2: Change sync preview from duplicate detection to entry resolution

**Files:**
- Modify: `src/ai_code_sessions/core.py`
- Modify: `tests/test_changelog_sync.py`

- [ ] **Step 1: Write failing tests for stable-identity lookup**

Add tests that lock in:

```python
def test_preview_returns_existing_run_id_when_same_session_grows(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    actor_dir = project_root / ".changelog" / "tester"
    actor_dir.mkdir(parents=True)
    source_jsonl = tmp_path / "rollout.jsonl"
    source_jsonl.write_text("{}", encoding="utf-8")

    entry = {
        "run_id": "run-existing",
        "tool": "codex",
        "start": "2026-01-01T00:00:00+00:00",
        "end": "2026-01-01T00:05:00+00:00",
        "source": {
            "kind": "native_session",
            "identity": {
                "tool": "codex",
                "session_id": "sess-123",
                "native_source_path": str(source_jsonl.resolve()),
                "start": "2026-01-01T00:00:00+00:00",
            },
        },
        "transcript": {
            "output_dir": None,
            "index_html": None,
            "source_jsonl": str(source_jsonl),
            "source_match_json": None,
        },
    }
    (actor_dir / "entries.jsonl").write_text(json.dumps(entry) + "\\n", encoding="utf-8")

    monkeypatch.setattr(
        core,
        "_canonical_session_identity_from_transcript",
        lambda **_: {
            "tool": "codex",
            "session_id": "sess-123",
            "native_source_path": str(source_jsonl.resolve()),
            "start": "2026-01-01T00:00:00+00:00",
        },
    )

    run_id, status = core._preview_changelog_append_status(
        tool="codex",
        project_root=project_root,
        session_dir=tmp_path / "session-dir",
        start="2026-01-01T00:00:00+00:00",
        end="2026-01-01T00:10:00+00:00",
        source_jsonl=source_jsonl,
        source_match_json=None,
        actor="tester",
    )

    assert run_id == "run-existing"
    assert status == "exists"
```

Run:

```bash
uv run --group dev pytest tests/test_changelog_sync.py -q
```

Expected: fail because preview still computes a fresh run id and checks a key that includes `end`.

- [ ] **Step 2: Implement stable-identity entry lookup**

Add helpers in `src/ai_code_sessions/core.py` along these lines:

```python
def _load_existing_entries_by_identity(entries_path: Path) -> dict[tuple[str, ...], dict]:
    entries_by_identity: dict[tuple[str, ...], dict] = {}
    if not entries_path.exists():
        return entries_by_identity
    with open(entries_path, "r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            identity_key = _session_identity_key(_entry_session_identity(entry))
            if identity_key is not None and identity_key not in entries_by_identity:
                entries_by_identity[identity_key] = entry
    return entries_by_identity


def _find_existing_changelog_entry_for_identity(*, entry_paths: tuple[Path, ...], identity_key: tuple[str, ...]) -> dict | None:
    for entry_path in entry_paths:
        entry = _load_existing_entries_by_identity(entry_path).get(identity_key)
        if entry is not None:
            return entry
    return None
```

Update `_preview_changelog_append_status` to:

- compute the stable logical identity
- search all repo-level entry files for a matching identity
- return the matched row’s `run_id` and `"exists"` when found
- return a freshly computed `run_id` and `"appended"` only when no identity match exists

- [ ] **Step 3: Run the targeted test file**

Run:

```bash
uv run --group dev pytest tests/test_changelog_sync.py -q
```

Expected: pass for the preview resolution cases.

### Task 3: Upsert sync-owned rows instead of appending duplicates

**Files:**
- Modify: `src/ai_code_sessions/core.py`
- Modify: `tests/test_changelog_sync.py`

- [ ] **Step 1: Write failing tests for sync-owned in-place updates**

Add tests that lock in:

```python
def test_generate_and_append_updates_existing_sync_owned_entry(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    actor_dir = project_root / ".changelog" / "tester"
    actor_dir.mkdir(parents=True)
    source_jsonl = tmp_path / "rollout.jsonl"
    source_jsonl.write_text("{}", encoding="utf-8")

    existing_entry = {
        "run_id": "run-existing",
        "created_at": "2026-01-01T00:06:00+00:00",
        "tool": "codex",
        "actor": "tester",
        "project": "repo",
        "project_root": str(project_root),
        "label": "Sync",
        "start": "2026-01-01T00:00:00+00:00",
        "end": "2026-01-01T00:05:00+00:00",
        "session_dir": str(tmp_path / "session-dir"),
        "continuation_of_run_id": None,
        "transcript": {
            "output_dir": None,
            "index_html": None,
            "source_jsonl": str(source_jsonl),
            "source_match_json": None,
        },
        "source": {
            "kind": "native_session",
            "identity": {
                "tool": "codex",
                "session_id": "sess-123",
                "native_source_path": str(source_jsonl.resolve()),
                "start": "2026-01-01T00:00:00+00:00",
            },
        },
        "summary": "old summary",
        "bullets": ["old bullet"],
        "tags": [],
        "touched_files": {"created": [], "modified": [], "deleted": [], "moved": []},
        "tests": [],
        "commits": [],
        "notes": None,
    }
    (actor_dir / "entries.jsonl").write_text(json.dumps(existing_entry) + "\\n", encoding="utf-8")

    monkeypatch.setattr(core, "_build_changelog_digest", lambda **_: {"delta": {"touched_files": {"created": [], "modified": [], "deleted": [], "moved": []}, "tests": [], "commits": []}})
    monkeypatch.setattr(core, "_run_codex_changelog_evaluator", lambda **_: {"summary": "new summary", "bullets": ["new bullet"], "tags": ["fix"], "notes": None})
    monkeypatch.setattr(core, "_preview_changelog_append_status", lambda **_: ("run-existing", "exists"))

    ok, run_id, status = core._generate_and_append_changelog_entry(
        tool="codex",
        label="Sync",
        cwd=str(project_root),
        project_root=project_root,
        session_dir=tmp_path / "session-dir",
        start="2026-01-01T00:00:00+00:00",
        end="2026-01-01T00:10:00+00:00",
        source_jsonl=source_jsonl,
        source_match_json=None,
        actor="tester",
        evaluator="codex",
        evaluator_model=None,
        claude_max_thinking_tokens=None,
        continuation_of_run_id=None,
        halt_on_429=False,
    )
    assert ok is True
    assert run_id == "run-existing"
    assert status == "updated"
```

Run:

```bash
uv run --group dev pytest tests/test_changelog_sync.py -q
```

Expected: fail because generation still treats `"exists"` as a no-op instead of an upsert opportunity for sync-owned rows.

- [ ] **Step 2: Implement in-place row rewriting for sync-owned entries**

Add focused rewrite helpers in `src/ai_code_sessions/core.py`:

```python
def _is_sync_owned_entry(entry: dict) -> bool:
    transcript = entry.get("transcript") if isinstance(entry, dict) else None
    source = entry.get("source") if isinstance(entry, dict) else None
    return bool(
        isinstance(source, dict)
        and source.get("kind") == "native_session"
        and isinstance(transcript, dict)
        and isinstance(transcript.get("source_jsonl"), str)
        and transcript.get("source_jsonl")
        and transcript.get("output_dir") is None
        and transcript.get("index_html") is None
    )


def _rewrite_matching_entry(*, entries_path: Path, run_id: str, transform: Callable[[dict], dict]) -> bool:
    raw_lines = entries_path.read_text(encoding="utf-8").splitlines(keepends=True)
    rewritten = False
    for idx, raw_line in enumerate(raw_lines):
        line = raw_line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("run_id") != run_id:
            continue
        raw_lines[idx] = json.dumps(transform(entry), ensure_ascii=False) + "\\n"
        rewritten = True
        break
    if rewritten:
        entries_path.write_text("".join(raw_lines), encoding="utf-8")
    return rewritten
```

Then update `_generate_and_append_changelog_entry` so:

- if preview says `"appended"`, append normally
- if preview says `"exists"` and the matched row is sync-owned, rewrite that row with the newly generated content and return `"updated"`
- if preview says `"exists"` and the matched row is export-owned, leave it untouched and return `"exists"`

Preserve:

- `run_id`
- `created_at`

Update:

- `end`
- summary fields
- touched file metadata
- tests and commits

- [ ] **Step 3: Run the targeted test file**

Run:

```bash
uv run --group dev pytest tests/test_changelog_sync.py -q
```

Expected: pass for append, exists, and updated cases.

### Task 4: Add a conservative repair command for existing duplicate native-sync rows

**Files:**
- Modify: `src/ai_code_sessions/cli.py`
- Modify: `src/ai_code_sessions/core.py`
- Modify: `tests/test_cli_changelog.py`

- [ ] **Step 1: Write failing CLI tests for dry-run and apply**

Add tests that lock in:

```python
def test_repair_native_sync_dry_run_reports_duplicates_without_rewriting(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli, ["changelog", "repair-native-sync", "--project-root", str(tmp_path), "--dry-run"])
    assert result.exit_code == 0
    assert "would collapse" in result.output.lower()


def test_repair_native_sync_apply_creates_backup_and_rewrites(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli, ["changelog", "repair-native-sync", "--project-root", str(tmp_path), "--apply"])
    assert result.exit_code == 0
    assert (tmp_path / ".changelog" / "tester" / "entries.jsonl.bak").exists()
```

Run:

```bash
uv run --group dev pytest tests/test_cli_changelog.py -q
```

Expected: fail because the repair command does not exist yet.

- [ ] **Step 2: Implement duplicate grouping and repair helpers**

Add helper structure in `src/ai_code_sessions/core.py` for:

```python
def _group_native_sync_duplicates(project_root: Path) -> list[dict]:
    return [
        {
            "group_key": ("path_start", "codex", "/abs/path/rollout.jsonl", "2026-01-01T00:00:00+00:00"),
            "entries": [{"run_id": "run-a"}, {"run_id": "run-b"}],
            "auto_repair": True,
            "reason": "same tool, native source path, and start",
        }
    ]


def _select_native_sync_group_winner(group: list[dict]) -> dict:
    def _winner_sort_key(entry: dict) -> tuple[int, str, str, str]:
        transcript = entry.get("transcript", {})
        richer_transcript = int(bool(transcript.get("index_html")) or bool(transcript.get("output_dir")))
        end_value = str(entry.get("end") or "")
        created_at = str(entry.get("created_at") or "")
        run_id = str(entry.get("run_id") or "")
        return (richer_transcript, end_value, created_at, run_id)

    return max(group, key=_winner_sort_key)
```

Rules:

- auto-group only when tool, normalized native source path, and normalized start all match
- prefer richer transcript rows before later `end`
- mark cross-actor groups and same-path-different-start groups as manual review

Then add `changelog repair-native-sync` in `src/ai_code_sessions/cli.py` with:

- `--dry-run` default behavior
- `--apply` required for writes
- `.jsonl.bak` backup before any rewrite

- [ ] **Step 3: Run the CLI test file**

Run:

```bash
uv run --group dev pytest tests/test_cli_changelog.py -q
```

Expected: pass for dry-run and apply behavior.

### Task 5: Update user-facing docs and run focused verification

**Files:**
- Modify: `docs/changelog.md`
- Modify: `docs/native-session-changelog-repair.md`

- [ ] **Step 1: Update docs to match the new lifecycle**

Revise `docs/changelog.md` so it no longer claims sync behavior is blanket append-only. Document instead:

```markdown
- Native-session sync uses stable logical session identity.
- Sync-owned rows are updated in place as the same native session grows.
- Export-owned rows remain the richer canonical transcript-backed representation when one already exists.
- `ais changelog repair-native-sync` is the maintenance path for cleaning existing duplicate sync rows.
```

- [ ] **Step 2: Run the focused verification suite**

Run:

```bash
uv run --group dev pytest tests/test_changelog_sync.py tests/test_cli_changelog.py -q
uv run --group dev ruff check src/ai_code_sessions/core.py src/ai_code_sessions/cli.py tests/test_changelog_sync.py tests/test_cli_changelog.py
uv run --group dev ruff format --check src/ai_code_sessions/core.py src/ai_code_sessions/cli.py tests/test_changelog_sync.py tests/test_cli_changelog.py
```

Expected:

- pytest passes
- Ruff check passes
- format check passes

- [ ] **Step 3: Manual repo replay before any broader release work**

Run:

```bash
uv run --project . ai-code-sessions changelog sync --codex --since "7 days ago" --project-root /Users/russronchi/Projects/ai-code-sessions --dry-run
uv run --project . ai-code-sessions changelog repair-native-sync --project-root /Users/russronchi/Projects/ai-code-sessions --dry-run
```

Expected:

- repeated sync dry-runs for the same session resolve to `exists`, not `appended`
- repair dry-run shows high-confidence duplicate groups separately from manual-review groups

- [ ] **Step 4: Commit**

Only if Russ explicitly approves git write commands in the current session:

```bash
git add docs/changelog.md docs/native-session-changelog-repair.md docs/todo/2026-04-03-native-session-changelog-repair-plan.md src/ai_code_sessions/core.py src/ai_code_sessions/cli.py tests/test_changelog_sync.py tests/test_cli_changelog.py
git commit -m "fix(changelog): stabilize native session sync identity"
```
