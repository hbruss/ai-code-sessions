# `ctx` wrapper (Codex + Claude)

## Goals

- Preserve Codex/Claude CLI rendering (colors, interactive UI) by **not** screen-scraping.
- Still produce per-repo, searchable, human-friendly session artifacts on exit:
  - Named directory (timestamp + label) inside the repo
  - Paginated HTML transcript (Simon-style)
  - Copy of the original native JSONL log file for archival
  - Debug info showing how the source log file was chosen

## What changed (high level)

`ctx` now has two modes depending on the tool:

1. **Codex / Claude**
   - Runs the CLI **normally** (no PTY transcription).
   - On exit, exports HTML transcript from the native JSONL logs using `ai-code-sessions export-latest`.

2. **Other tools (`--tool …`)**
   - Keeps the old PTY transcription logger behavior (writes `events.jsonl`, `transcript.md`, etc.).

## Output directories

When you run `ctx "My title" --codex` in a repo, it writes to:

`<repo-root>/.codex/sessions/<STAMP>_<SANITIZED_TITLE>[_N]/`

When you run `ctx "My title" --claude`, it writes to:

`<repo-root>/.claude/sessions/<STAMP>_<SANITIZED_TITLE>[_N]/`

Notes:

- `STAMP` defaults to Pacific time (`America/Los_Angeles`) unless `CTX_TZ` is set.
- If a directory already exists (common with concurrent sessions), `ctx` appends `_<N>` to avoid collisions.

### Important: exporter location vs. output location

The exporter project (this repo) can live anywhere on disk.

The output directory is always created inside **the repo you run `ctx` in** (resolved via `git rev-parse --show-toplevel`).

Example:

- Exporter lives at: `/anywhere/ai-code-sessions`
- You run `ctx "Fix checkout" --codex` inside: `/work/ShopRepo`
- Output goes to: `/work/ShopRepo/.codex/sessions/..._Fix_checkout/`

## Artifacts generated (Codex/Claude)

For Codex/Claude sessions, `ctx` generates:

- `index.html` — transcript index page
- `page-001.html`, `page-002.html`, … — paginated transcript pages
- `source_match.json` — which native JSONL file was selected and why (top candidates)
- `rollout-*.jsonl` (Codex) or `<uuid>.jsonl` (Claude) — copied native log file (archival)

## Resume / continue

Both Codex and Claude can resume an existing conversation. `ctx` supports this and will try to **reuse the previous session directory** (so the transcript gets updated in-place instead of creating a new timestamped folder every time).

### Codex

```bash
ctx "My label" --codex resume
ctx "My label" --codex resume <session-id>
```

### Claude Code

```bash
ctx "My label" --claude --continue
ctx "My label" --claude --resume <session-id>
```

Notes:

- On resume/continue, `ctx` tries to find the previous session directory by label (and session ID if provided) and writes the updated `index.html`/`page-*.html` there.
- If `claude --fork-session` is used, `ctx` treats it as a new session and creates a new output directory.

## `ctx open`

`ctx open --latest-codex` and `ctx open --latest-claude` now prefer:

1. `index.html` (new transcript)
2. fallback to `trace.html` (legacy PTY export sessions)

`ctx open --latest-*` looks under the **current repo’s** `.codex/sessions` or `.claude/sessions`, so run it from within the repo you care about (or pass the session directory explicitly).

## Configuration

### Where the exporter project lives

`ctx` needs to know where the `ai-code-sessions` project directory is so it can run:

`uv run --project <path> ai-code-sessions …`

By default it assumes:

`$HOME/Projects/ai-code-sessions`

If your clone lives somewhere else, set this once in your shell profile:

```bash
export CTX_TRANSCRIPTS_PROJECT="/absolute/path/to/ai-code-sessions"
```

### Time zone for the session folder name

```bash
export CTX_TZ="America/Los_Angeles"
```

### Enable changelog generation

`ai-code-sessions` can append an entry to `.changelog/<actor>/entries.jsonl` after each export (see `docs/changelog.md`).

Enable this for `ctx` runs by setting:

```bash
export CTX_ACTOR="your-github-username"
export CTX_CHANGELOG=1
```

## Where the code lives

`ctx` is currently implemented as a user-local script (outside this repo):

- `/Users/russronchi/bin/ctx.sh`

Any time we change it, we create a timestamped backup next to it (example):

- `/Users/russronchi/bin/ctx.sh.bak-20260102-010800`
