# Troubleshooting

## `ctx` didn’t generate `index.html`

Check the session directory for:

- `source_match.json` (should exist for Codex/Claude sessions)
- a copied `rollout-*.jsonl` / `*.jsonl` source file (if `--json` ran successfully)

If `source_match.json` is missing, the export step likely didn’t run.

## Common warnings / errors

If you don’t have `ai-code-sessions` installed on your `PATH`, run it via:

```bash
uv run --project "$CTX_TRANSCRIPTS_PROJECT" ai-code-sessions <command> ...
```

### `ctx: warning: uv not found; skipping transcript export`

Install `uv`, or ensure it’s on your `PATH`.

### `ctx: warning: transcript export failed`

Open `source_match.json` in the session directory and look for:

- `best.path` (what it selected)
- `candidates` (other nearby options)

If `candidates` is empty or missing, the exporter likely couldn’t find matching log files.

### Changelog generation/backfill produced only `failures.jsonl`

Changelog generation is best-effort and runs `codex exec` under the hood.

1. Open `.changelog/<actor>/failures.jsonl` and read the `error` field (it includes a `stderr_tail` for the actionable part).
2. Confirm `codex` is installed and logged in:

```bash
codex --version
codex login --help
```

If you’re running in a sandboxed environment without network access, the export step can still succeed while changelog generation fails (by design).

### `No matching Codex rollout files found`

Confirm Codex is writing rollout files:

```bash
ls -R ~/.codex/sessions | head
```

Then try exporting directly from a known rollout file:

```bash
ai-code-sessions json ~/.codex/sessions/YYYY/MM/DD/rollout-XYZ.jsonl -o ./out --open
```

### `No matching Claude session files found`

Confirm Claude is writing local session files:

```bash
ls -R ~/.claude/projects | head
```

Then try exporting directly from a known JSONL file:

```bash
ai-code-sessions json ~/.claude/projects/<encoded>/*.jsonl -o ./out --open
```

## A transcript picked the wrong session

This usually happens when:

- you ran multiple sessions concurrently in the same repo/subdir, and their timestamps overlap closely
- the `cwd` in the native log doesn’t match what you expected (e.g. you started `ctx` in a subdirectory)

Steps:

1. Inspect `source_match.json` to see what it picked.
2. If the correct JSONL is listed under `candidates`, re-export from that file using `ai-code-sessions json ... -o <session-dir>`.
3. If the correct JSONL is not listed at all, widen the time window and rerun `find-source --debug-json ...` to see what exists nearby.

## `ctx open --latest-codex` opens the wrong repo’s session

`ctx open --latest-*` finds the “latest session” under the **current repo’s** `.codex/sessions` or `.claude/sessions`.

Run it from within the repo you care about, or pass the session directory explicitly:

```bash
ctx open /path/to/repo/.codex/sessions/<session>
```

## Legacy PTY sessions

Older `ctx` sessions (PTY transcription mode) won’t have `index.html`. They usually have `trace.html`.

`ctx open <session> html` prefers:

1. `index.html` (new native-log exporter)
2. `trace.html` (legacy)
