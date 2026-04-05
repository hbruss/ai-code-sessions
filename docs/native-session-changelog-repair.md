# Native Session Changelog Repair

Status: implemented in repo

Last updated: 2026-04-03 22:53 PDT

## Problem

`ais changelog sync` was intended to be idempotent for native Codex and Claude sessions, but the current native-session dedup key is not stable. The current identity includes the session `end` timestamp, so a live session that grows on disk is treated as a new identity on each sync pass.

This produces duplicate changelog rows for one logical Codex or Claude session. The bug is systemic across repos, including this repository and downstream repos already inspected.

## Verified Evidence

- Current code includes `end` in the canonical native-session identity in [src/ai_code_sessions/core.py](/Users/russronchi/Projects/ai-code-sessions/src/ai_code_sessions/core.py#L1091).
- Current dedup keys also include `end` in [src/ai_code_sessions/core.py](/Users/russronchi/Projects/ai-code-sessions/src/ai_code_sessions/core.py#L1289).
- Sync preview decides `exists` vs `appended` using that key in [src/ai_code_sessions/core.py](/Users/russronchi/Projects/ai-code-sessions/src/ai_code_sessions/core.py#L1482).
- OpenAI’s Codex docs and App Server docs describe a durable session or thread that can be resumed, forked, and archived. They do not define “latest observed `end` timestamp” as a session identity:
  - https://developers.openai.com/codex/cli/features/#resuming-conversations
  - https://developers.openai.com/codex/cli/reference/#codex-resume
  - https://developers.openai.com/codex/app-server/#api-overview
- Current local `codex-cli 0.118.0` rollout files still begin with a `session_meta` event whose payload contains a stable `id`, which is a better identity anchor than `end`.

## Design Goals

- Stop creating new duplicate rows for the same logical native session.
- Preserve one canonical changelog row per logical native session.
- Allow a sync-owned row to be refreshed as the underlying native session grows.
- Keep repo-wide dedup behavior consistent across actor directories.
- Repair existing duplicate rows conservatively, with dry-run and backup-first behavior.

## Non-Goals

- No backward-compatibility shim for undocumented native log layouts beyond the current extractor requirements.
- No automatic migration of ambiguous duplicate groups.
- No attempt to preserve the current "append-only for every sync-owned row forever" behavior. That behavior is the bug.

## Current Incorrect Model

Today the native-session identity is effectively:

```text
(tool, native_source_path, start, end)
```

That model is wrong for active sessions because `end` is mutable observation state, not logical identity.

## Correct Model

### 1. Stable logical session identity

The canonical logical identity for a native session is:

```text
if session_id is available:
    ("session_id", tool, session_id)
else:
    ("path_start", tool, normalized_native_source_path, normalized_start)
```

Rules:

- `session_id` is preferred whenever the native source exposes it.
- `native_source_path` must be normalized to an absolute resolved path.
- `start` must be normalized to canonical UTC ISO-8601.
- `end` is not part of identity.

### 2. Native source metadata shape

The current `source` object remains the source of truth for sync-owned entries, but the identity payload changes meaningfully.

Target shape:

```json
{
  "kind": "native_session",
  "identity": {
    "tool": "codex",
    "session_id": "019d56e1-d679-7d01-a301-8af80085a8e8",
    "native_source_path": "/Users/russronchi/.codex/sessions/2026/04/03/rollout-2026-04-03T22-05-34-019d56e1-d679-7d01-a301-8af80085a8e8.jsonl",
    "start": "2026-04-04T05:05:34.336000+00:00"
  }
}
```

Notes:

- `session_id` is optional in storage because not every source shape guarantees it forever.
- `end` is intentionally excluded from `source.identity`.
- Existing fallback reconstruction logic should reinterpret old entries into the new stable identity model instead of trusting stored old identity payloads literally.

### 3. Sync ownership model

An entry is sync-owned when all of the following are true:

- `source.kind == "native_session"`
- `transcript.source_jsonl` is present
- `transcript.output_dir` is `null`
- `transcript.index_html` is `null`

This matters because sync-owned rows are mutable while export-owned rows are richer and should not be silently replaced by a poorer row.

## Forward Sync Lifecycle

For a discovered native session and resolved project root:

1. Build the stable logical identity.
2. Search all repo changelog entry files for an existing entry with the same stable identity.
3. If no identity match exists, append a new sync-owned entry.
4. If a sync-owned identity match exists, rewrite that row in place with the newest observed session state.
5. If an export-owned identity match exists, treat the session as already represented and do not append a second row.

### Fields updated during sync-owned upsert

When rewriting an existing sync-owned row:

- Update `end` to the latest observed end.
- Recompute summary, bullets, tags, touched files, tests, commits, and notes from the newest digest.
- Preserve `run_id`.
- Preserve `created_at`.
- Preserve actor and project routing.

Rationale:

- Preserving `run_id` avoids churn in references and CLI output.
- Preserving `created_at` keeps the row’s original insertion time stable.
- The row content becomes the latest known representation of the same logical session.

### Preview semantics

`_preview_changelog_append_status` should return:

- `("existing_run_id", "exists")` when a stable-identity match already exists, including sync-owned rows that would be updated.
- `("new_run_id", "appended")` only when no stable-identity match exists.

This prevents the current misleading behavior where a later observation computes a new `run_id` even though it should resolve to the already tracked logical session.

## Cross-Actor and Cross-Shape Behavior

- Identity matching is repo-global, not actor-local.
- If the same stable identity exists in another actor directory, sync should treat that as already represented and skip automatic append.
- Automatic mutation is limited to the matched sync-owned row in the actor file being rewritten.
- Cross-actor duplicates are cleanup territory, not forward-sync mutation territory.

## Cleanup Strategy

Cleanup is a separate maintenance operation. It must not be bundled into normal sync.

### Command shape

Recommended command:

```bash
ais changelog repair-native-sync --dry-run
ais changelog repair-native-sync --apply
```

### Default behavior

- Default mode is report-only.
- `--apply` is required for rewrites.
- Every rewritten `entries.jsonl` gets a `.jsonl.bak` backup first.

### Auto-repair grouping

A group is safe for automatic collapse only when:

- tool matches
- normalized native source path matches
- normalized start matches

Groups with the same path but different starts are not auto-collapsed.

### Winner selection

For an auto-repair group, select the retained row using:

1. Prefer richer transcript ownership over poorer transcript ownership.
2. Prefer later canonical `end`.
3. Prefer later `created_at`.
4. Prefer lexicographically stable `run_id` as final tie-breaker.

Practical effect:

- Export-owned entries beat sync-owned entries when both represent the same logical session.
- Otherwise, keep the latest observed sync-owned row.

### Manual-review groups

The repair command should report but not rewrite:

- cross-actor duplicate groups
- same native source path with different starts
- groups missing enough source metadata to form a stable identity

## Acceptance Criteria

Forward fix:

- Re-syncing the same native source with the same session identity and a later `end` returns `exists`, not `appended`.
- Re-syncing the same native source with the same stable identity updates the existing sync-owned row in place.
- A same-path, different-start session remains distinct.
- Dry-run and real run make the same append-versus-exists decision.
- Repo-global duplicate detection works across actor directories.

Cleanup:

- Dry-run reports candidate groups without rewriting files.
- Apply mode creates `.jsonl.bak` before rewrite.
- Re-running cleanup after apply is idempotent.
- Ambiguous groups are skipped and reported clearly.

## Files Changed By The Implementation

- [src/ai_code_sessions/core.py](/Users/russronchi/Projects/ai-code-sessions/src/ai_code_sessions/core.py)
- [src/ai_code_sessions/cli.py](/Users/russronchi/Projects/ai-code-sessions/src/ai_code_sessions/cli.py)
- [tests/test_changelog_sync.py](/Users/russronchi/Projects/ai-code-sessions/tests/test_changelog_sync.py)
- [tests/test_cli_changelog.py](/Users/russronchi/Projects/ai-code-sessions/tests/test_cli_changelog.py)
- [docs/changelog.md](/Users/russronchi/Projects/ai-code-sessions/docs/changelog.md)

## Notes For Implementation

- The implementation should fail fast if the native extractor can no longer recover a stable identity from current Codex or Claude native logs.
- The repair tool should reuse the existing backup-and-rewrite pattern already present in changelog maintenance commands instead of inventing a second rewrite mechanism.
- Documentation must be updated to remove the inaccurate blanket claim that sync behavior is append-only. The correct statement is: export/backfill entries remain append-oriented, but sync-owned native-session rows are upserted by stable logical identity.
