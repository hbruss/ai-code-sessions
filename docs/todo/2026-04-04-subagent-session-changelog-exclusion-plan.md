# Subagent Session Changelog Exclusion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent explicit Codex subagent sessions from entering changelog sync, add a separate conservative cleanup command for already-synced subagent rows, and fully update user-facing docs for both this change and the recent Claude long-context transport change.

**Architecture:** Add a narrow provenance helper around Codex native `session_meta` so discovery can classify primary versus subagent sessions before building sync candidates. Keep historical cleanup separate from duplicate repair by adding a dedicated `repair-subagent-sync` command that proves provenance from `transcript.source_jsonl`, reports by default, creates `.jsonl.bak` backups on apply, and only removes rows with explicit `payload.source.subagent.thread_spawn` evidence. Finish with a full docs pass across `README.md` and the changelog/config/repair docs so the released behavior is accurately documented in one place.

**Tech Stack:** Python 3.11, Click, pathlib, json/jsonl parsing, pytest, Ruff

---

## File Map

- Modify: `src/ai_code_sessions/core.py`
  - add a small Codex native provenance helper
  - exclude explicit subagent sessions in `_discover_native_codex_sessions(...)`
  - add a separate subagent-cleanup grouping/report helper for changelog rows
- Modify: `src/ai_code_sessions/cli.py`
  - add `ais changelog repair-subagent-sync`
  - reuse the current report/apply/backup UX shape without overloading `repair-native-sync`
- Modify: `tests/test_changelog_sync.py`
  - lock in forward discovery/sync exclusion behavior
- Modify: `tests/test_cli_changelog.py`
  - lock in dry-run/apply output, backup behavior, and conservative manual review behavior
- Modify: `README.md`
  - update top-level changelog feature description and operational guidance
- Modify: `docs/changelog.md`
  - document subagent exclusion, the new repair command, and the already-shipped Claude prompt artifact / long-context behavior
- Modify: `docs/config.md`
  - document Claude changelog evaluator defaults and any relevant model-selection wording
- Modify: `docs/native-session-changelog-repair.md`
  - document the split between duplicate repair and subagent cleanup
- Modify: `docs/todo/2026-04-04-claude-changelog-long-context-transport-plan.md`
  - mark the docs follow-through complete if implementation includes the required docs pass

### Task 1: Add explicit Codex subagent provenance classification and forward sync exclusion

**Files:**
- Modify: `src/ai_code_sessions/core.py`
- Test: `tests/test_changelog_sync.py`

- [ ] **Step 1: Write the failing provenance and discovery tests**

Add focused tests to `tests/test_changelog_sync.py` that prove top-level Codex sessions remain discoverable and explicit subagent sessions do not enter the candidate list:

```python
def test_discover_native_codex_sessions_excludes_explicit_subagent_rollouts(tmp_path, monkeypatch):
    sessions_base = tmp_path / ".codex" / "sessions" / "2026" / "04" / "04"
    sessions_base.mkdir(parents=True)
    top_level = sessions_base / "rollout-top-level.jsonl"
    subagent = sessions_base / "rollout-subagent.jsonl"

    _write_jsonl(
        top_level,
        [
            {
                "type": "session_meta",
                "timestamp": "2026-04-04T18:00:00Z",
                "payload": {
                    "id": "top-level-session",
                    "timestamp": "2026-04-04T18:00:00Z",
                    "cwd": str(tmp_path / "repo"),
                    "source": "cli",
                },
            },
            {"type": "event_msg", "timestamp": "2026-04-04T18:05:00Z"},
        ],
    )
    _write_jsonl(
        subagent,
        [
            {
                "type": "session_meta",
                "timestamp": "2026-04-04T18:10:00Z",
                "payload": {
                    "id": "subagent-session",
                    "timestamp": "2026-04-04T18:10:00Z",
                    "cwd": str(tmp_path / "repo"),
                    "source": {
                        "subagent": {
                            "thread_spawn": {
                                "parent_thread_id": "parent-123",
                                "depth": 1,
                                "agent_nickname": "Singer",
                                "agent_role": "worker",
                            }
                        }
                    },
                },
            },
            {"type": "event_msg", "timestamp": "2026-04-04T18:11:00Z"},
        ],
    )

    monkeypatch.setattr(core, "_user_codex_sessions_dir", lambda: tmp_path / ".codex" / "sessions")

    candidates = core._discover_native_codex_sessions(
        since=datetime(2026, 4, 4, 0, 0, tzinfo=timezone.utc),
        until=datetime(2026, 4, 5, 0, 0, tzinfo=timezone.utc),
    )

    assert [candidate["session_id"] for candidate in candidates] == ["top-level-session"]
```

```python
def test_discover_native_codex_sessions_limit_window_not_polluted_by_subagents(...):
    ...
    assert [candidate["source_jsonl"] for candidate in candidates] == [str(expected_top_level.resolve())]
```

Run:

```bash
uv run --group dev pytest tests/test_changelog_sync.py -q -k "subagent_rollouts or polluted_by_subagents"
```

Expected: fail because discovery currently returns both top-level and subagent rollout files.

- [ ] **Step 2: Add a narrow provenance helper in `core.py`**

Add a helper near `_parse_codex_rollout_jsonl(...)` that only reads the first `session_meta` payload and classifies provenance:

```python
def _codex_rollout_session_meta_payload(source_jsonl: Path) -> dict | None:
    try:
        with source_jsonl.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") != "session_meta":
                    continue
                payload = obj.get("payload")
                return payload if isinstance(payload, dict) else None
    except OSError:
        return None
    return None


def _codex_rollout_is_subagent_session(source_jsonl: Path) -> bool:
    payload = _codex_rollout_session_meta_payload(source_jsonl)
    source = payload.get("source") if isinstance(payload, dict) else None
    return (
        isinstance(source, dict)
        and isinstance(source.get("subagent"), dict)
        and isinstance(source["subagent"].get("thread_spawn"), dict)
    )
```

Keep this helper intentionally narrow. It should not infer from prompt text, labels, or `agent_role` alone.

- [ ] **Step 3: Exclude explicit subagent sessions in `_discover_native_codex_sessions(...)`**

Insert the filter before candidate construction:

```python
for path in sorted(day_dir.glob("rollout-*.jsonl")):
    resolved_path = path.resolve()
    if resolved_path in seen_paths:
        continue
    seen_paths.add(resolved_path)
    if _codex_rollout_is_subagent_session(resolved_path):
        continue
    start_dt, end_dt, cwd, session_id = _codex_rollout_session_times(resolved_path)
    ...
```

Do not add heuristics or change Claude discovery. The only forward exclusion is explicit Codex subagent provenance.

- [ ] **Step 4: Add a sync-level regression test for mixed top-level and subagent history**

Add a higher-level test in `tests/test_changelog_sync.py` that sync-like candidate processing only sees the top-level session when both exist:

```python
def test_discover_native_sessions_codex_returns_only_top_level_candidates(...):
    candidates = core._discover_native_sessions(
        tools=("codex",),
        since=...,
        until=...,
    )

    assert [candidate["session_id"] for candidate in candidates] == ["top-level-session"]
```

Run:

```bash
uv run --group dev pytest tests/test_changelog_sync.py -q -k "only_top_level_candidates or subagent_rollouts"
```

Expected: pass.

### Task 2: Add explicit-provenance grouping for historical subagent cleanup

**Files:**
- Modify: `src/ai_code_sessions/core.py`
- Test: `tests/test_cli_changelog.py`

- [ ] **Step 1: Write failing CLI-facing tests for explicit subagent cleanup classification**

Add new tests in `tests/test_cli_changelog.py` that lock in four behaviors:

```python
def test_changelog_repair_subagent_sync_dry_run_reports_explicit_subagent_rows(tmp_path):
    ...
    assert result.exit_code == 0
    assert "auto_repair_groups=1" in result.output
    assert "run_id=subagent-run-1" in result.output
    assert "parent_thread_id=parent-123" in result.output
    assert "agent_role=worker" in result.output
    assert "rewritten_entries=0" in result.output
```

```python
def test_changelog_repair_subagent_sync_missing_source_jsonl_is_manual_review(tmp_path):
    ...
    assert "manual_review_groups=1" in result.output
    assert "reason=missing_source_jsonl" in result.output
```

```python
def test_changelog_repair_subagent_sync_top_level_rows_are_not_removed(tmp_path):
    ...
    assert "auto_repair_groups=0" in result.output
    assert "rewritten_entries=0" in result.output
```

```python
def test_changelog_repair_subagent_sync_apply_rewrites_only_explicit_rows(tmp_path):
    ...
    assert "rewritten_files=1" in result.output
    assert "rewritten_entries=2" in result.output
    assert entries_path.with_suffix(".jsonl.bak").exists()
```

Run:

```bash
uv run --group dev pytest tests/test_cli_changelog.py -q -k "repair_subagent_sync"
```

Expected: fail because the command and classifier do not exist.

- [ ] **Step 2: Add a dedicated report helper in `core.py`**

Create a separate helper beside `_group_native_sync_duplicates_for_repair(...)` rather than extending it:

```python
def _group_subagent_sync_rows_for_repair(*, project_root: Path, actor: str | None = None) -> dict:
    auto_repair_groups: list[dict] = []
    manual_review_groups: list[dict] = []
    skipped_groups: list[dict] = []

    for actor_dir, entries_path in _iter_changelog_entries_files(project_root=project_root, actor=actor):
        for line_index, entry in _iter_entries_jsonl_rows(entries_path):
            transcript_path = _entry_transcript_source_jsonl(entry)
            if transcript_path is None:
                manual_review_groups.append(...)
                continue

            payload = _codex_rollout_session_meta_payload(transcript_path)
            if payload is None:
                manual_review_groups.append(...)
                continue

            if not _codex_rollout_is_subagent_session(transcript_path):
                skipped_groups.append(...)
                continue

            auto_repair_groups.append(...)

    return {
        "auto_repair_groups": auto_repair_groups,
        "manual_review_groups": manual_review_groups,
        "skipped_groups": skipped_groups,
    }
```

Required record fields:

```python
{
    "run_id": entry.get("run_id"),
    "actor": entry.get("actor"),
    "ownership": _changelog_entry_ownership(entry),
    "created_at": entry.get("created_at"),
    "end": entry.get("end"),
    "entries_path": entries_path,
    "line_index": line_index,
    "source_jsonl": str(transcript_path),
    "parent_thread_id": parent_thread_id,
    "agent_role": agent_role,
    "agent_nickname": agent_nickname,
}
```

Important policy:
- classify from transcript provenance only
- do not infer from label text
- do not merge this logic into duplicate repair

- [ ] **Step 3: Group safe deletions conservatively**

Use one-row groups for explicit rows so the command reports exactly what it will remove:

```python
auto_repair_groups.append(
    {
        "reason": "explicit_subagent_source",
        "record": record,
    }
)
```

Use manual-review reasons like:

```python
"missing_source_jsonl"
"unreadable_source_jsonl"
"missing_session_meta"
"non_codex_source_jsonl"
```

Do not auto-remove anything when provenance is missing or ambiguous.

- [ ] **Step 4: Run the focused cleanup tests**

Run:

```bash
uv run --group dev pytest tests/test_cli_changelog.py -q -k "repair_subagent_sync"
```

Expected: pass.

### Task 3: Add the `repair-subagent-sync` CLI command with backup-first apply behavior

**Files:**
- Modify: `src/ai_code_sessions/cli.py`
- Test: `tests/test_cli_changelog.py`

- [ ] **Step 1: Add failing output-shape expectations**

Extend the Task 2 tests to assert the detailed default output includes per-row metadata:

```python
assert "AUTO reason=explicit_subagent_source actor=alice entries=1" in result.output
assert "entry run_id=subagent-run-1 ownership=sync actor=alice" in result.output
assert "source_jsonl=" in result.output
assert "parent_thread_id=parent-123" in result.output
assert "agent_nickname=Singer" in result.output
```

Expected: fail until the CLI formatter exists.

- [ ] **Step 2: Implement dedicated formatter helpers in `cli.py`**

Add helpers parallel to the native-sync formatter, not by overloading it:

```python
def _repair_subagent_sync_record_line(label: str, record: dict | None) -> str | None:
    if not isinstance(record, dict):
        return None
    return (
        f"  {label} "
        f"run_id={record.get('run_id')} "
        f"ownership={record.get('ownership')} "
        f"actor={record.get('actor')} "
        f"end={record.get('end')} "
        f"created_at={record.get('created_at')} "
        f"file={record.get('entries_path')} "
        f"line={record.get('line_index')} "
        f"source_jsonl={record.get('source_jsonl')} "
        f"parent_thread_id={record.get('parent_thread_id')} "
        f"agent_role={record.get('agent_role')} "
        f"agent_nickname={record.get('agent_nickname')}"
    )
```

- [ ] **Step 3: Add the new command and reuse the current safe rewrite flow**

Add a command beside `repair-native-sync`:

```python
@changelog_cli.command("repair-subagent-sync")
@click.option("--project-root", help="Target git repo root (defaults to git toplevel of CWD).")
@click.option("--actor", help="Filter by actor. If not specified, inspects all actors.")
@click.option("--dry-run", is_flag=True, help="Report subagent-derived rows without writing files.")
@click.option("--apply", is_flag=True, help="Apply safe repairs by removing explicit subagent-derived rows.")
def changelog_repair_subagent_sync_cmd(project_root, actor, dry_run, apply):
    root = Path(project_root).resolve() if project_root else (_git_toplevel(Path.cwd()) or Path.cwd().resolve())
    report = _group_subagent_sync_rows_for_repair(project_root=root, actor=actor)
    ...
```

Apply behavior should match current safety expectations:

```python
for entries_path in sorted(removals_by_file):
    backup_path = entries_path.with_suffix(".jsonl.bak")
    shutil.copy2(entries_path, backup_path)
    rewritten = _rewrite_entries_file_removing_lines(
        entries_path=entries_path,
        remove_line_indexes=line_indexes,
    )
```

Do not touch rows outside the explicit-provenance set.

- [ ] **Step 4: Verify idempotence and non-overlap with duplicate repair**

Add one regression test:

```python
def test_changelog_repair_subagent_sync_second_apply_is_noop(tmp_path):
    ...
    first = runner.invoke(cli, [..., "--apply"])
    second = runner.invoke(cli, [..., "--apply"])

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert "auto_repair_groups=0" in second.output
    assert "rewritten_entries=0" in second.output
```

Run:

```bash
uv run --group dev pytest tests/test_cli_changelog.py -q -k "repair_subagent_sync or repair_native_sync"
```

Expected: pass, and the existing native-sync repair tests remain green.

### Task 4: Review and update the docs surface for both major changelog changes

**Files:**
- Modify: `README.md`
- Modify: `docs/changelog.md`
- Modify: `docs/config.md`
- Modify: `docs/native-session-changelog-repair.md`
- Modify: `docs/todo/2026-04-04-claude-changelog-long-context-transport-plan.md`

- [ ] **Step 1: Add failing doc assertions where practical**

Add lightweight assertions to existing CLI or doc-adjacent tests only if the repo already has a stable pattern for that content. Otherwise, keep this as a manual verification task and avoid brittle string-match tests.

Document the required content checklist in the plan execution notes:

```text
- README mentions native sync behavior, subagent exclusion, and the new cleanup command
- changelog docs explain forward exclusion vs duplicate repair vs subagent cleanup
- config docs mention Claude changelog evaluator defaulting to opus[1m]
- changelog docs mention repo-local .tmp/.archive prompt artifact behavior and budget fallback
```

- [ ] **Step 2: Update `README.md`**

Add or revise a concise user-facing section that explains:

```markdown
- `ais changelog sync` ignores explicit Codex subagent sessions
- `ais changelog repair-native-sync` repairs duplicate sync rows
- `ais changelog repair-subagent-sync` removes already-synced explicit subagent rows
- Claude changelog evaluation uses the long-context Claude model path and preserves failed prompt artifacts for debugging
```

Keep the README high-level and point detailed operational guidance to `docs/changelog.md`.

- [ ] **Step 3: Update `docs/changelog.md` and `docs/native-session-changelog-repair.md`**

Ensure these docs clearly separate the three maintenance behaviors:

```markdown
1. forward sync
2. duplicate repair (`repair-native-sync`)
3. subagent cleanup (`repair-subagent-sync`)
```

Also document the already-shipped Claude behavior:

```markdown
- full prompt artifacts are written under `.tmp/changelog-eval/`
- failed prompt artifacts are retained under `.archive/changelog-eval/`
- Claude changelog evaluation defaults to `opus[1m]`
- budget retry happens only after a full-prompt failure
```

- [ ] **Step 4: Update `docs/config.md` and the prior transport plan record**

In `docs/config.md`, make sure Claude changelog evaluator configuration matches the shipped code:

```markdown
- default Claude changelog model: `opus[1m]`
- explicit per-repo or env overrides still win when configured
```

In `docs/todo/2026-04-04-claude-changelog-long-context-transport-plan.md`, add a short implementation record note that the docs pass was completed together with the subagent-exclusion work, so the plan record does not falsely imply that documentation was still pending.

- [ ] **Step 5: Run a final docs and behavior verification pass**

Run:

```bash
uv run --group dev pytest tests/test_changelog_sync.py tests/test_cli_changelog.py -q
uv run --group dev ruff check src/ai_code_sessions/core.py src/ai_code_sessions/cli.py tests/test_changelog_sync.py tests/test_cli_changelog.py
uv run --group dev ruff format --check src/ai_code_sessions/core.py src/ai_code_sessions/cli.py tests/test_changelog_sync.py tests/test_cli_changelog.py
```

Then do a manual docs sanity pass:

```bash
rg -n --max-count 80 "repair-subagent-sync|repair-native-sync|subagent|opus\\[1m\\]|\\.tmp/changelog-eval|\\.archive/changelog-eval" README.md docs
```

Expected:
- tests pass
- Ruff passes
- docs references are internally consistent and mention both the transport change and the new subagent behavior

## Self-Review Notes

- Spec coverage:
  - forward discovery-time exclusion: Task 1
  - separate cleanup command: Tasks 2-3
  - explicit provenance only / no heuristics: Tasks 1-3
  - conservative apply and manual-review categories: Tasks 2-3
  - comprehensive docs update including `README.md` and the prior Claude transport work: Task 4
- Placeholder scan:
  - no `TODO`/`TBD` placeholders remain
  - all tasks name exact files and concrete commands
- Consistency:
  - forward filter uses `_codex_rollout_is_subagent_session(...)`
  - cleanup uses the same transcript-provenance helper, not a second heuristic path
