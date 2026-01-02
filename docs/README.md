# ai-code-sessions (project docs)

This repo contains a small system for generating **nice, browsable HTML transcripts** from native session logs produced by:

- **OpenAI Codex CLI**
- **Claude Code CLI**

It’s designed to work with your existing `ctx` workflow:

- You start a session with a human-friendly label (e.g. `ctx "Fix login bug" --codex`)
- You work normally (with full terminal colors/UI intact)
- When you quit, `ctx` writes a per-repo session directory (timestamp + label) and auto-generates:
  - `index.html` + `page-*.html` transcript pages
  - `source_match.json` explaining which raw log file was used
  - a copy of the original native JSONL log file for archival

This documentation covers:

- What gets generated and where (`ctx` output layout)
- How the exporter works (CLI)
- How we match the correct native JSONL source when multiple sessions run concurrently
- Troubleshooting and common failure modes

## Quick links

- `docs/ctx.md` — how `ctx` works now (colors preserved + auto export)
- `docs/cli.md` — CLI commands and examples
- `docs/source-matching.md` — how matching works (important for concurrency)
- `docs/troubleshooting.md` — what to do when export fails
- `docs/privacy.md` — what’s captured and how to treat it safely
- `docs/changelog.md` — optional changelog generation from sessions
- `docs/development.md` — tests, local dev, and architecture notes

## Reference implementation

The directory `docs/claude-code-transcripts/` is a vendored copy of Simon Willison’s `claude-code-transcripts` project (Apache-2.0). It is used as a reference.

Our working implementation lives here:

- `src/ai_code_sessions/__init__.py`
