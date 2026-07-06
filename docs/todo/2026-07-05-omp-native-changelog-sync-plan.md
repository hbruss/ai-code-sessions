# OMP Native Changelog Sync Plan

Date: 2026-07-05
Status: Draft

## Goal

Add first-class `oh-my-pi` / OMP session support to `ais changelog sync` so OMP-driven coding sessions can produce normal `.changelog/<actor>/entries.jsonl` rows alongside Codex and Claude sessions.

The implementation should support:

- forward sync from OMP native JSONL logs
- explicit one-session backfill from a known OMP JSONL path
- a manual recovery path for future OMP sessions outside the normal scan window
- stable native-session identity so reruns upsert rather than duplicate
- docs and tests that keep OMP source-tool support separate from the existing Codex/Claude evaluator choice

## Source Artifacts

- Local OMP package: `/Users/russronchi/.bun/install/global/node_modules/@oh-my-pi/pi-coding-agent`
- OMP session schema reference: `src/session/session-entries.ts` in the installed OMP package
- OMP session path reference: `src/session/session-paths.ts` in the installed OMP package
- Current `ai-code-sessions` sync code:
  - `src/ai_code_sessions/core.py`
  - `src/ai_code_sessions/cli.py`
  - `docs/changelog.md`
  - `tests/test_changelog_sync.py`
  - `tests/test_cli_changelog.py`
- Real local backfill target:
  - Project: `/Users/russronchi/Projects/crypto-trading`
  - OMP JSONL: `/Users/russronchi/.omp/agent/sessions/-Projects-crypto-trading/2026-07-05T06-19-22-925Z_019f30ee-79ad-7000-88bd-910ef11ec13e.jsonl`
  - Advisor sidecar: `/Users/russronchi/.omp/agent/sessions/-Projects-crypto-trading/2026-07-05T06-19-22-925Z_019f30ee-79ad-7000-88bd-910ef11ec13e/__advisor.jsonl`

## Verified Current State

- `ai-code-sessions` currently treats native sync as Codex/Claude-only.
- `ais ctx` accepts only `codex` or `claude`.
- `parse_session_file(...)` detects Codex rollout JSONL, otherwise falls back to Claude JSONL parsing.
- The changelog entry schema currently allows `tool` values `codex`, `claude`, and `unknown`, but not `omp`.
- Native discovery currently has `_discover_native_codex_sessions(...)` and `_discover_native_claude_sessions(...)`, but no OMP discovery.
- Native session-id recovery currently knows only Codex and Claude source formats.
- Local OMP sessions live under `~/.omp/agent/sessions/<encoded-project>/`.
- The local `crypto-trading` OMP session has:
  - session id `019f30ee-79ad-7000-88bd-910ef11ec13e`
  - cwd `/Users/russronchi/Projects/crypto-trading`
  - title `Resume magi BTC data-system build`
  - start `2026-07-05T06:19:22.925Z`
  - end `2026-07-05T18:49:20.273Z`
  - model messages from `anthropic/claude-fable-5`
  - 7 user messages, 194 assistant messages, 218 tool results
  - tool calls including `edit`, `write`, `bash`, `read`, `eval`, `todo`, `job`, `grep`, `glob`, and `ask`
- OMP also writes advisor sidecars such as `__advisor.jsonl`; those should not become primary changelog rows by default.
- `crypto-trading` currently has no OMP changelog rows; its latest entries are older Codex rows.

## Decisions And Assumptions

- Use `tool: "omp"` for OMP-sourced changelog rows. Do not fake OMP as `claude` even when the model is Claude/Fable.
- Keep evaluator selection unchanged. `--evaluator claude` or config-driven Claude evaluation should continue to mean "use Claude to summarize this session", not "source logs came from Claude Code".
- Add `--omp` to `ais changelog sync`.
- Make `--all` include OMP once discovery and tests are in place, unless implementation reveals that OMP scanning is too broad or noisy.
- Add an explicit source path option for manual recovery/backfill. Proposed UX:

```bash
ais changelog sync --omp --source-jsonl /path/to/session.jsonl --project-root "$PWD"
```

- OMP advisor sidecars and subagent/advisor JSONL files are excluded from primary discovery in this slice.
- Preserve `transcript.source_jsonl` as the source of truth for sync rows. HTML export support for OMP can be a follow-up unless required for tests.
- Prefer synthetic OMP fixtures in tests. Do not vendor real Russ transcripts into the test suite.

## Out Of Scope

- Adding `ais ctx --omp` wrapper support.
- Changing OMP itself.
- Importing OMP `history.db`, `agent.db`, usage stats, or memory/hindsight data.
- Summarizing advisor sidecars into primary changelog rows.
- Reworking existing Codex/Claude performance beyond what is necessary to avoid regressions.
- Historical cleanup of bad OMP rows, since no OMP rows exist yet.

## Plan

- [ ] Add OMP as a recognized source tool.
  - Extend changelog schema and validation to allow `tool: "omp"`.
  - Extend query/filter docs and CLI choices where the user filters by source tool.
  - Keep evaluator choices limited to current evaluator implementations unless a separate OMP evaluator is later introduced.

- [ ] Add minimal OMP JSONL parser support.
  - Detect OMP JSONL from the initial `title` slot or `session` header shape.
  - Parse the `session` header for `id`, `cwd`, `timestamp`, `title`, and `version`.
  - Convert OMP `message` entries into the existing normalized logline shape.
  - Map OMP `message.content[].type == "text"` to text blocks.
  - Map OMP `message.content[].type == "thinking"` to thinking blocks.
  - Map OMP `message.content[].type == "toolCall"` to tool-use blocks.
  - Map OMP `message.role == "toolResult"` to tool-result blocks.
  - Ignore OMP `custom` rows except where they provide useful metadata already represented by paired tool calls/results.

- [ ] Add OMP native session metadata helpers.
  - Implement `_omp_session_times(source_jsonl)` returning start, end, cwd, and session id.
  - Prefer the `session` header id for stable identity.
  - Use max timestamp across parseable top-level entries as the end time.
  - Fall back to file mtime only if the JSONL lacks usable end timestamps.
  - Teach `_native_session_id_for_source(...)` to recover OMP session ids.

- [ ] Add OMP discovery.
  - Implement `_user_omp_sessions_dir()` with default `~/.omp/agent/sessions`.
  - Add an override for non-default/profile installs, e.g. `CTX_OMP_SESSIONS_DIR`.
  - Discover `*.jsonl` under the OMP sessions root.
  - Exclude nested sidecar files such as `__advisor.jsonl`.
  - Do not rely on the encoded project directory name for targeting; read `cwd` from the session header.
  - Reuse existing window-overlap and repo-resolution logic.

- [ ] Add explicit one-source sync/backfill.
  - Add a `--source-jsonl` option to `ais changelog sync` for OMP.
  - When `--source-jsonl` is present, build one candidate from that file instead of scanning.
  - Require the selected tool to match the file shape, or fail fast with a clear diagnostic.
  - Support dry-run with the explicit source path.

- [ ] Preserve existing sync semantics.
  - Generate canonical native-session identity using `tool=omp`, normalized source path, start, and OMP session id.
  - Rerunning sync for the same growing or already-finished OMP session should update an existing sync-owned row rather than append duplicates.
  - If a richer export-owned row already exists for the same OMP session in the future, sync should leave it alone, matching current native behavior.

- [ ] Add tests with compact synthetic fixtures.
  - Parser test: OMP session header, model changes, user message, assistant text/thinking/tool call, tool result.
  - Times test: recover start/end/cwd/session id from OMP JSONL.
  - Discovery test: discover top-level OMP session and exclude `__advisor.jsonl`.
  - CLI test: `ais changelog sync --omp --dry-run --project-root <repo>`.
  - CLI test: `ais changelog sync --omp --source-jsonl <file> --dry-run --project-root <repo>`.
  - Identity test: same OMP source/session id resolves to one canonical key.
  - Regression test: existing Codex/Claude sync tests still pass.

- [ ] Update docs and packaged skill material.
  - Update `README.md` and `docs/changelog.md` with OMP sync and explicit-source examples.
  - Update `docs/cli.md` command tables where source-tool choices are listed.
  - Update packaged changelog skill docs if they mention only Codex/Claude source tools.
  - Add troubleshooting notes for "OMP session not found" and non-default `CTX_OMP_SESSIONS_DIR`.

- [ ] Verify on the real local OMP session in dry-run mode.
  - Run the dev CLI against the known `crypto-trading` OMP JSONL with `--dry-run`.
  - Confirm the resolved repo is `/Users/russronchi/Projects/crypto-trading`.
  - Confirm the planned tool is `omp`, label/title is recovered, and source path points at the OMP JSONL.
  - Confirm the advisor sidecar is not selected as a separate primary candidate.

- [ ] Backfill after Russ opens the write gate.
  - Run the explicit-source command without `--dry-run` for the `crypto-trading` session.
  - Inspect the new `.changelog/<actor>/entries.jsonl` row.
  - Run `ais changelog lint` in `crypto-trading`.
  - Confirm rerunning the same command reports existing/updated rather than appended duplicate.

## Verification

- [ ] `uv run --group dev pytest tests/test_changelog_sync.py -q`
- [ ] `uv run --group dev pytest tests/test_cli_changelog.py -q`
- [ ] `uv run --group dev pytest tests/test_core_parsing_pipelines.py -q`
- [ ] `uv run --group dev pytest -q`
- [ ] `uv run --group dev ruff check src tests`
- [ ] `uv run --group dev ruff format --check src tests`
- [ ] `uv run --project . ai-code-sessions changelog sync --omp --source-jsonl /Users/russronchi/.omp/agent/sessions/-Projects-crypto-trading/2026-07-05T06-19-22-925Z_019f30ee-79ad-7000-88bd-910ef11ec13e.jsonl --project-root /Users/russronchi/Projects/crypto-trading --dry-run`
- [ ] After write gate only: run the explicit-source command without `--dry-run`, then run `ais changelog lint` in `/Users/russronchi/Projects/crypto-trading`.

## Handoff Notes

Paste-ready short restart message:

```text
We need to implement OMP native changelog sync in /Users/russronchi/Projects/ai-code-sessions. Start by reading docs/todo/2026-07-05-omp-native-changelog-sync-plan.md. The goal is first-class `ais changelog sync --omp`, explicit `--source-jsonl` backfill/manual recovery, and one backfill target for /Users/russronchi/Projects/crypto-trading from the OMP JSONL at /Users/russronchi/.omp/agent/sessions/-Projects-crypto-trading/2026-07-05T06-19-22-925Z_019f30ee-79ad-7000-88bd-910ef11ec13e.jsonl. Do not mutate git or write the target crypto-trading changelog until Russ opens that gate.
```
