# ai-code-sessions

Generate clean, browsable HTML transcripts from native session logs produced by:

- **OpenAI Codex CLI**
- **Claude Code CLI**

This project is based on Simon Willison’s `claude-code-transcripts` (Apache-2.0) and reuses the same rendering approach (paginated HTML with tool calls + outputs).

Supported sources:

- **Codex**: `~/.codex/sessions/**/rollout-*.jsonl`
- **Claude Code**: `~/.claude/projects/**/<session-id>.jsonl`

## Quick start (recommended): `ctx` wrapper

If you use `/Users/russronchi/bin/ctx.sh`, point it at this repo (once in your shell profile):

```bash
export CTX_TRANSCRIPTS_PROJECT="/absolute/path/to/ai-code-sessions"
```

Then run it inside any git repo:

```bash
ctx "Fix checkout" --codex
ctx "Investigate flaky test" --claude
```

When you quit the session, `ctx` creates a timestamped + labeled directory inside that repo:

- `<repo>/.codex/sessions/<STAMP>_<LABEL>/`
- `<repo>/.claude/sessions/<STAMP>_<LABEL>/`

Each directory includes:

- `index.html` + `page-*.html` transcript pages
- `source_match.json` showing how the native JSONL source was selected
- a copy of the original native JSONL log file for archival

## Optional: changelog entries

`ai-code-sessions` can append a concise, engineering-oriented entry after export.

Enable it for `ctx` runs:

```bash
export CTX_ACTOR="your-github-username"
export CTX_CHANGELOG=1
```

It writes to the project repo:

- `.changelog/<actor>/entries.jsonl` (successes)
- `.changelog/<actor>/failures.jsonl` (non-fatal failures)

## Run the CLI directly

This repo exposes a CLI via `uv` (no global install required):

```bash
uv run --project /absolute/path/to/ai-code-sessions ai-code-sessions --help
```

The older command name `ai-code-transcripts` still works (it’s an alias).

Convert a specific JSON/JSONL file:

```bash
uv run --project . ai-code-sessions json /path/to/session.jsonl -o ./out --label "My label" --json --open
```

Export by time window (what `ctx` uses):

```bash
uv run --project . ai-code-sessions export-latest \
  --tool codex \
  --cwd "$PWD" \
  --project-root "$(git rev-parse --show-toplevel)" \
  --start 2026-01-02T07:25:55.212Z \
  --end 2026-01-02T09:16:57.576Z \
  -o ./out \
  --label "My label" \
  --json \
  --changelog
```

## Docs

Start at `docs/README.md`.
