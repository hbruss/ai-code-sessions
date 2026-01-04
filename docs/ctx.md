# `ais ctx` (Codex + Claude)

`ais ctx` runs Codex CLI or Claude Code CLI **normally** (no PTY screen-scraping), then exports the matching native JSONL session log into a per-repo directory with paginated HTML.

## Install

```bash
pipx install ai-code-sessions
pipx ensurepath
```

## Usage

Start a labeled session:

```bash
ais ctx "Fix checkout race condition" --codex
ais ctx "Investigate flaky CI tests" --claude
```

Pass-through arguments are forwarded to the underlying CLI (resume/continue included):

```bash
ais ctx "My label" --codex resume
ais ctx "My label" --codex resume <session-id>

ais ctx "My label" --claude --continue
ais ctx "My label" --claude --resume <session-id>
```

Notes:

- On resume/continue, `ais ctx` tries to reuse the previous session directory (by label, and session ID when provided) so the transcript is updated in-place.
- If Claude is invoked with `--fork-session`, it’s treated as a new session (new output directory).

## Output directories

- Codex:  `<repo-root>/.codex/sessions/<STAMP>_<SANITIZED_LABEL>[_N]/`
- Claude: `<repo-root>/.claude/sessions/<STAMP>_<SANITIZED_LABEL>[_N]/`

`STAMP` defaults to Pacific time (`America/Los_Angeles`) unless overridden by config or `CTX_TZ`.

## Artifacts generated

Each output directory includes:

- `index.html`, `page-*.html` — transcript pages
- `source_match.json` — why the source JSONL was selected (candidates + scoring)
- copied native JSONL (`rollout-*.jsonl` for Codex or `<uuid>.jsonl` for Claude)
- `export_runs.jsonl` — export metadata (used for resumable backfills)

## Configuration

Run `ais setup` to write:

- Global config: OS-specific user config dir
- Per-repo config: `<repo-root>/.ai-code-sessions.toml` (or `.ais.toml`)

Environment variables override config:

- `CTX_TZ`, `CTX_CODEX_CMD`, `CTX_CLAUDE_CMD`
- `CTX_CHANGELOG`, `CTX_ACTOR`, `CTX_CHANGELOG_EVALUATOR`, `CTX_CHANGELOG_MODEL`, `CTX_CHANGELOG_CLAUDE_THINKING_TOKENS`

## Optional: shell alias

If you prefer typing `ctx`:

```bash
alias ctx='ais ctx'
```
