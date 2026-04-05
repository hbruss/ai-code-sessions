# Subagent Session Changelog Exclusion

Status: proposed

Last updated: 2026-04-04 19:54 PDT

## Problem

`ais changelog sync` currently treats top-level Codex sessions and spawned Codex subagent sessions as equally eligible native sessions.

That is producing noisy repo changelogs. In this repository, a single sync appended 35 rows, and 33 of them were explicit subagent sessions. The two meaningful top-level rows were buried among worker, reviewer, explorer, and aborted-dispatch sessions.

The user’s policy is now explicit:

- Subagent sessions should be ignored for changelog purposes.
- Historical cleanup should remove only rows that are explicitly subagent-derived.
- Top-level design or review sessions should remain, even if they are somewhat noisy.

## Verified Evidence

### 1. Subagent sessions are explicitly marked in native Codex metadata

Top-level native Codex sessions begin with `session_meta.payload.source = "cli"` or another non-subagent source value.

Spawned subagent sessions begin with explicit metadata like:

```json
{
  "source": {
    "subagent": {
      "thread_spawn": {
        "parent_thread_id": "...",
        "depth": 1,
        "agent_nickname": "Singer",
        "agent_role": "worker"
      }
    }
  },
  "agent_nickname": "Singer",
  "agent_role": "worker"
}
```

This marker is present in the first `session_meta` object of the native Codex rollout JSONL, so the signal is available before sync builds changelog candidates.

Relevant code:

- [src/ai_code_sessions/core.py](/Users/russronchi/Projects/ai-code-sessions/src/ai_code_sessions/core.py#L4579)
- [src/ai_code_sessions/core.py](/Users/russronchi/Projects/ai-code-sessions/src/ai_code_sessions/core.py#L4744)

### 2. Current sync path does not filter subagents

Codex native discovery currently:

1. scans `rollout-*.jsonl`
2. reads session timing/cwd/session_id
3. builds a native session candidate
4. returns the candidate to changelog sync

There is no subagent exclusion in `_discover_native_codex_sessions`.

Relevant code:

- [src/ai_code_sessions/core.py](/Users/russronchi/Projects/ai-code-sessions/src/ai_code_sessions/core.py#L4744)
- [src/ai_code_sessions/core.py](/Users/russronchi/Projects/ai-code-sessions/src/ai_code_sessions/core.py#L4651)

### 3. Sync consumes discovered candidates directly

`changelog sync` obtains candidates from `_discover_native_sessions(...)` and then resolves them into repo-targeted sync/appended/skipped decisions.

That means a late filter in preview or append would still allow subagent sessions to occupy discovery ordering and `--limit` windows.

Relevant code:

- [src/ai_code_sessions/cli.py](/Users/russronchi/Projects/ai-code-sessions/src/ai_code_sessions/cli.py#L1667)
- [src/ai_code_sessions/cli.py](/Users/russronchi/Projects/ai-code-sessions/src/ai_code_sessions/cli.py#L1719)
- [src/ai_code_sessions/core.py](/Users/russronchi/Projects/ai-code-sessions/src/ai_code_sessions/core.py#L4828)

### 4. Historical bad rows can be identified from explicit transcript provenance

Existing changelog entries already store `transcript.source_jsonl`.

That means a historical cleanup pass can inspect each row’s native JSONL and decide whether the row is explicitly subagent-derived by rereading the first `session_meta` object.

Relevant code:

- [src/ai_code_sessions/core.py](/Users/russronchi/Projects/ai-code-sessions/src/ai_code_sessions/core.py#L3835)
- [src/ai_code_sessions/cli.py](/Users/russronchi/Projects/ai-code-sessions/src/ai_code_sessions/cli.py#L2496)

### 5. Existing repair-native-sync machinery is duplicate-oriented, not classification-oriented

The current repair flow groups rows by native-session identity collisions. It is built to collapse duplicates conservatively, not to remove rows based on a “this row should never have been synced” classification.

Relevant code:

- [src/ai_code_sessions/core.py](/Users/russronchi/Projects/ai-code-sessions/src/ai_code_sessions/core.py#L1725)
- [src/ai_code_sessions/cli.py](/Users/russronchi/Projects/ai-code-sessions/src/ai_code_sessions/cli.py#L2496)

## Goals

- Prevent future sync from appending subagent-native sessions to changelogs.
- Base exclusion on explicit native session metadata, not summaries or prompt heuristics.
- Provide a separate cleanup path for already-synced subagent rows.
- Keep cleanup report-first, backup-first, and conservative.
- Preserve top-level sessions, including top-level design/review sessions.
- Review and update the user-facing docs surface, including `README.md`, for both this change and the recently shipped Claude changelog long-context transport change.

## Non-Goals

- No removal of top-level sessions solely because they are low-value or verbose.
- No heuristic filtering based on prompt text like “You are the implementer”.
- No mutation of changelog entries whose source JSONL cannot be verified explicitly.
- No bundling of this cleanup into `repair-native-sync`.

## Options

### Option 1: Discovery-time exclusion plus separate cleanup command

Forward path:

- Extend Codex-native discovery to classify a rollout as `primary` or `subagent` from the first `session_meta`.
- Skip subagent sessions before candidate construction in `_discover_native_codex_sessions`.

Historical cleanup:

- Add a separate report/apply command, for example:

```bash
ais changelog repair-subagent-sync --dry-run
ais changelog repair-subagent-sync --apply
```

- It scans changelog entries, rereads `transcript.source_jsonl`, and removes only rows whose native metadata explicitly proves subagent origin.

Pros:

- Most robust forward fix.
- Prevents subagents from affecting sync ordering, `--limit`, dry-run summaries, and append decisions.
- Cleanup semantics remain separate from duplicate-collapse semantics.
- Matches current CLI safety style well.

Cons:

- Adds a second repair-style command.
- Requires a small amount of new native session classification plumbing.

### Option 2: Sync-loop exclusion plus separate cleanup command

Forward path:

- Keep discovery unchanged.
- Exclude candidates later in `changelog sync` after `_discover_native_sessions(...)` returns them.

Historical cleanup:

- Same separate cleanup command as Option 1.

Pros:

- Slightly less code churn in discovery helpers.

Cons:

- Weaker behavior.
- Subagent sessions still occupy candidate slots and distort `--limit` behavior.
- Sync loop has to rediscover or re-open per-session metadata later anyway.
- Makes the exclusion policy feel bolted on rather than native to session discovery.

### Option 3: Heuristic text-based exclusion plus extension of `repair-native-sync`

Forward path:

- Exclude sessions when prompt summaries or first user messages look like subagent tasks.

Historical cleanup:

- Extend `repair-native-sync` to also remove subagent rows.

Pros:

- Reuses an existing command name.

Cons:

- Wrong source of truth.
- Text heuristics are brittle and can create false positives.
- Overloads a duplicate-repair command with unrelated classification semantics.
- Harder to explain and test.

This option should not be chosen.

## Recommendation

Choose **Option 1**.

Reasoning:

- The forward filter belongs in Codex-native discovery because the explicit subagent marker already exists there.
- The cleanup should be a separate command because this is not duplicate collapse; it is explicit provenance-based removal of rows that should never have been synced.
- This gives one canonical policy:
  - forward sync ignores explicit subagent sessions
  - historical cleanup removes explicit subagent-derived rows only when the provenance can be verified

## Proposed Design

### 1. Add native session classification for Codex discovery

Introduce a small helper that reads the first `session_meta` payload and classifies the session:

```text
primary:
    first.session_meta.payload.source is not a subagent object

subagent:
    first.session_meta.payload.source.subagent exists
```

Recommended candidate metadata:

```json
{
  "session_kind": "primary" | "subagent"
}
```

Only the normalized classification should flow forward. The sync loop does not need the full raw payload.

### 2. Exclude subagents in `_discover_native_codex_sessions`

Behavior:

1. read first `session_meta`
2. classify session
3. if `session_kind == "subagent"`, skip the file entirely
4. otherwise continue with timing extraction and candidate construction

This keeps the filter scoped to native Codex discovery and prevents subagent sessions from polluting candidate ordering.

### 3. Preserve current top-level behavior

Top-level sessions remain eligible even if they are:

- design-oriented
- review-oriented
- release-oriented
- somewhat noisy

The only forward exclusion is explicit subagent provenance.

### 4. Add a separate historical cleanup command

Recommended command:

```bash
ais changelog repair-subagent-sync --dry-run
ais changelog repair-subagent-sync --apply
```

Scope:

- inspect actor `entries.jsonl` files in the target repo
- for each row, inspect `transcript.source_jsonl`
- classify the native source JSONL
- if explicit subagent provenance is proven, report or remove that row

### 5. Historical cleanup eligibility rules

A row is eligible only when all of the following are true:

1. `transcript.source_jsonl` exists and is readable
2. the source JSONL is a native Codex rollout with `session_meta`
3. `session_meta.payload.source.subagent` is present

Rows are not auto-removed when:

- `source_jsonl` is missing
- the source file cannot be read
- the file is not in the expected Codex native shape
- the metadata is ambiguous

Those rows should be reported for manual review, not deleted.

### 6. Cleanup reporting model

Use a report/apply flow parallel to the existing repair command.

Recommended categories:

- `AUTO`: explicit subagent-derived rows safe to remove
- `MANUAL`: ambiguous rows where proof is incomplete
- `SKIP`: rows outside scope

Per-row metadata should include:

- `run_id`
- actor
- ownership
- `created_at`
- `end`
- `entries.jsonl` path
- line index
- `source_jsonl`
- agent role/nickname when available

### 7. Cleanup apply behavior

Apply mode should:

- create `.jsonl.bak` for each rewritten `entries.jsonl`
- remove only the exact loser lines identified as explicit subagent rows
- never rewrite rows outside the explicit-provenance set

### 8. Command relationship to `repair-native-sync`

Do not extend `repair-native-sync`.

Keep the conceptual split:

- `repair-native-sync`: collapse duplicate logical native sessions
- `repair-subagent-sync`: remove rows that were invalid sync candidates due to explicit subagent provenance

This separation keeps both commands easier to reason about and safer to audit.

## Edge Cases

### Missing source JSONL

If a changelog row points to a non-existent `transcript.source_jsonl`, do not remove it automatically.

Reason:

- the cleanup command no longer has proof of provenance

Action:

- report as `MANUAL`

### Exported or copied subagent rows

If a row is not sync-owned but its `transcript.source_jsonl` still points to a native Codex rollout with explicit subagent metadata, it is still eligible for report/apply cleanup.

Reason:

- the user’s policy is about subagent origin, not ownership type

### Future tool support

Claude already excludes `agent-*` files in native discovery.

This design should not try to normalize a cross-tool abstraction beyond what is needed now. If future Codex/Claude metadata evolves, the classification helper can be extended then.

## Acceptance Criteria

Forward behavior:

- Sync no longer appends explicit Codex subagent sessions.
- Top-level Codex sessions still sync normally.
- Repo-resolution behavior for top-level sessions is unchanged.
- `--limit` behavior is no longer distorted by skipped subagent candidates.

Historical cleanup:

- Dry-run reports explicit subagent-derived rows without rewriting files.
- Apply mode creates `.jsonl.bak` before rewriting any `entries.jsonl`.
- Only explicit subagent-derived rows are auto-removed.
- Rows lacking explicit proof are reported, not deleted.
- Re-running cleanup after apply is idempotent.

## Testing Implications

Forward tests should cover:

- top-level Codex rollout remains discoverable
- subagent Codex rollout is excluded from discovery
- sync against a repo with mixed top-level and subagent sessions appends only the top-level rows

Cleanup tests should cover:

- dry-run report of explicit subagent rows
- apply mode backup creation and line removal
- missing `source_jsonl` becomes manual review
- top-level rows are never classified as subagent-derived
- idempotent second apply

## Recommended Next Step

If approved, the implementation plan should be split into two tasks:

1. Forward exclusion in Codex-native discovery plus sync test coverage
2. Separate `repair-subagent-sync` cleanup command plus report/apply tests

Documentation work should be planned as a first-class task, not left as a follow-up note. It needs to review and update `README.md` plus the affected changelog/config/repair docs so the shipped behavior matches the current product.
