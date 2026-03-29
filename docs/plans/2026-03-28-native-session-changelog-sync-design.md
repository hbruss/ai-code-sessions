# Native Session Changelog Sync Design

**Date:** 2026-03-28

## Goal

Make changelog generation the primary `ai-code-sessions` workflow by allowing `ais` to discover recent native Codex and Claude sessions directly, resolve the correct project repo safely, and append missing `.changelog` entries without requiring `ais ctx`.

## User Constraints

- Default scan scope should be recent only: the last 48 hours.
- The scan window must be configurable with a CLI flag.
- The tool must not silently write to the wrong repo-local `.changelog`.
- If repo targeting is ambiguous, the CLI should prompt interactively rather than guessing.
- The workflow should work after native Codex or Claude usage without requiring the `ais ctx` wrapper.
- Transcript HTML export should become an optional separate workflow rather than a required side effect of changelog generation.
- Implementation will happen in the main repo, not a worktree.

## Problem

Today the highest-value part of the product is the append-only changelog system, but the implementation is still centered on `ais ctx` session directories. That creates two mismatches with actual usage:

- the user often runs Codex or Claude directly rather than through `ais ctx`
- changelog generation currently assumes a wrapper-managed session artifact even though the native session logs are the real source of truth

This makes the best part of the product dependent on the least-used part of the workflow. It also means the current "normal" path is stronger for HTML transcript export than for changelog capture, even though changelog capture is the capability the user actually relies on every session.

## Approved Product Direction

### 1. Make changelog sync the primary workflow

Add a first-class command that scans recent native sessions and appends missing changelog entries:

```bash
ais changelog sync --codex
ais changelog sync --claude
ais changelog sync --all
```

This command becomes the default daily workflow. It must be safe to run repeatedly after each coding session.

### 2. Decouple transcript export from changelog generation

HTML transcript generation remains supported, but it is no longer the center of the product. It should become an explicit, optional workflow. The native-session discovery and repo-resolution engine introduced for changelog sync should be reusable for a future transcript sync/export command.

### 3. Keep existing commands, but demote them

`ais ctx`, `ais export-latest`, and `ais changelog backfill` remain supported. They are still useful for wrapper-managed workflows, historical recovery, and deterministic exports, but they are no longer the recommended default for day-to-day changelog capture.

## Scope

### In Scope

- a new `ais changelog sync` command
- direct discovery of native Codex and Claude sessions
- default 48-hour scan window with CLI overrides
- safe repo resolution with confidence gates
- interactive prompting when repo resolution is ambiguous
- duplicate detection that works even when prior entries were created through `ais ctx`
- changelog generation directly from native session logs
- CLI docs and tests for the new workflow

### Out of Scope for the First Implementation

- removing `ais ctx`
- removing HTML transcript export
- shipping a native transcript sync/export command in the same change
- introducing a global database or index of discovered sessions
- automatic migration of historical `.changelog` files beyond the metadata needed for duplicate detection

## CLI Design

### Primary command

```bash
ais changelog sync [--codex|--claude|--all]
```

### Default behavior

- scan native sessions from the last 48 hours
- resolve each session to a target repo
- skip sessions already represented in that repo's `.changelog`
- append entries only for missing sessions
- prompt interactively when the repo is ambiguous
- report unresolved sessions without writing

### Flags

- `--codex`: scan Codex native sessions only
- `--claude`: scan Claude native sessions only
- `--all`: scan both native session sources
- `--since <relative-or-absolute-time>`: override the default 48-hour window
- `--until <relative-or-absolute-time>`: optional upper bound for the scan window
- `--limit <n>`: process at most `n` sessions after filtering
- `--dry-run`: print planned actions without writing changelog entries
- `--project-root <path>`: narrow processing to sessions that resolve to the given repo
- `--actor <name>`: override the actor recorded in appended entries
- `--evaluator <codex|claude>`: select changelog evaluator
- `--model <name>`: override evaluator model

### Command naming

This should be a new `sync` command, not a new meaning for `backfill`.

`backfill` implies historical recovery. The proposed workflow is the normal, repeated, daily operation of the tool, so the product language should say `sync`.

## Native Session Discovery Design

### Source of truth

The source of truth becomes the native session logs, not `ais ctx` session directories.

For Codex, discovery should reuse the existing native session helpers and JSONL parsing support already present in `core.py`.

For Claude, discovery should reuse the existing local session format support rather than introducing a new importer path.

### Candidate selection

The sync command should:

1. enumerate candidate native session files for the requested tool(s)
2. extract enough metadata to determine session start/end time and identity
3. keep only sessions that overlap the requested time window
4. sort candidates by session end time, newest first by default

The implementation should reuse existing time-extraction helpers where possible rather than inventing new timestamp parsing rules.

### Session unit

Each native session file is the canonical unit of work for sync.

The command should not require a wrapper-created output directory, copied JSONL file, `source_match.json`, or `export_runs.jsonl` artifact in order to create a changelog entry.

## Repo Resolution Design

### Evidence bundle

For each native session candidate, build a structured evidence bundle:

- tool: `codex` or `claude`
- native session path
- session start and end timestamps
- session id if available
- session `cwd` if available
- git toplevel resolved from `cwd`, if valid
- any session git metadata already present in the log, such as repository URL, branch, or commit
- any tool-specific project identifier, such as Claude's encoded project directory
- a short prompt summary for interactive disambiguation

### Confidence levels

#### High confidence

The session can be written automatically when:

- the session `cwd` resolves to a local git toplevel
- the evidence is internally consistent
- the resolved repo is not filtered out by `--project-root`

This is the only case where `ais changelog sync` should write without prompting.

#### Medium confidence

The session has one or more plausible target repos, but the evidence is not strong enough for silent writes.

In this case, `ais` should prompt interactively with a short chooser showing:

- candidate repo path
- why it is a candidate
- session time
- tool
- short prompt summary

The selected repo becomes the target for that session only. No global cache is required in the first version.

#### Low confidence

No trustworthy repo can be derived.

In this case, `ais` must not write a changelog entry. It should report the session as unresolved and continue.

### Safety rules

- Never silently use the current shell repo unless the session evidence resolves there.
- Never auto-write based on weak signals such as session label text.
- If evidence conflicts, downgrade to prompt instead of choosing.
- If `--project-root` is supplied and the inferred repo does not match it, skip the session with a clear message.
- `--dry-run` must print the confidence level, key evidence, and planned action.

## Duplicate Detection Design

### Problem with the current model

The current append path computes `run_id` partly from the `ais ctx` session directory. That is not a stable identity for native-session sync, and it also risks duplicates when the same native session was previously changelogged through `ais ctx`.

### New canonical identity

Add a canonical session identity that is independent of wrapper-managed session directories.

The identity should be derived from native-session facts such as:

- tool
- native session id when available
- normalized native source path
- first and last message timestamps

New changelog entries written by sync should record this identity in entry metadata so future sync runs can skip them quickly.

### Compatibility with existing entries

Sync must also avoid duplicating sessions that were already changelogged through `ais ctx` or `backfill`.

To do that, duplicate detection should:

1. load existing changelog entries for the resolved repo
2. inspect each entry's stored transcript source JSONL path when present
3. derive the same canonical session identity from that stored source log when possible
4. compare the native candidate identity against those derived identities before appending

This avoids requiring a one-time changelog migration just to make sync safe.

## Changelog Generation Design

### Entry generation path

Changelog generation should reuse the existing digest and evaluator pipeline as much as possible:

- parse the native session log
- build the bounded digest
- run the selected evaluator
- validate and append the entry

The main change is the entry context:

- `session_dir` can no longer be assumed to exist
- transcript HTML is optional and should not be required for changelog creation
- source metadata must point back to the native session identity

### Entry metadata

New sync-generated entries should include enough source metadata to support later duplicate detection and optional transcript export. At minimum this should include:

- native tool
- canonical session identity
- native source path or a stable reference to it
- session start and end timestamps

The schema change should be additive so older entries remain readable.

For compatibility with existing readers, sync-generated entries should still include a `transcript` object. The compatibility rule should be:

- `transcript.source_jsonl` remains required
- `transcript.output_dir` may be `null`
- `transcript.index_html` may be `null`
- `transcript.source_match_json` may be `null`

This preserves the existing refresh and lint workflows, which need a readable `source_jsonl`, while allowing changelog entries to exist without a generated HTML transcript.

## Future Transcript Workflow

The same native discovery and repo-resolution engine should be designed for later reuse by an explicit transcript command such as:

```bash
ais transcript sync --codex
```

That follow-up command is intentionally deferred. The first implementation should focus on changelog sync only.

## Implementation Notes

### Code structure direction

The implementation should move toward four clearer concerns:

- native session discovery
- repo resolution and confidence scoring
- duplicate detection / sync state
- artifact generation, such as changelog append or transcript export

This can happen incrementally. The first pass does not need a full module split, but it should avoid adding more `ctx`-specific coupling to `cli.py` and `core.py`.

### Reuse first

Prefer reusing existing helpers for:

- native session time extraction
- parsing Codex and Claude logs
- relative-date resolution
- actor detection
- evaluator execution
- changelog validation

Add new helpers only where the current code is specifically coupled to `ais ctx`.

## Testing and Verification Design

### Automated tests

Add tests that cover:

- default 48-hour sync window behavior
- `--since` filtering
- Codex-only, Claude-only, and combined scans
- high-confidence repo resolution writes automatically
- medium-confidence repo resolution prompts interactively
- low-confidence sessions are reported and skipped
- `--project-root` narrowing
- duplicate detection against sync-generated entries
- duplicate detection against prior `ais ctx`-generated entries
- sync-generated entries with `null` HTML transcript fields and a valid `source_jsonl`
- refresh and lint compatibility for sync-generated entries
- dry-run output

### Manual verification

Manually verify with:

- a native Codex session in one repo
- a native Claude session in one repo
- at least one ambiguous session that requires prompt resolution
- a repeated sync run showing idempotent skip behavior

## Open Questions

### Prompt persistence

The first implementation does not need to remember interactive repo choices across runs. If repeated prompting becomes annoying, a later change can add an optional cache keyed by canonical session identity.
