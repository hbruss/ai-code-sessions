# CLI

This project’s CLI converts native session logs into clean, mobile-friendly HTML transcripts with pagination.

Command names:

- `ai-code-sessions` (preferred)
- `ai-code-transcripts` (alias)

It supports:

- **Codex CLI** rollout logs (`~/.codex/sessions/**/rollout-*.jsonl`)
- **Claude Code** local logs (`~/.claude/projects/**/<uuid>.jsonl`)
- Claude web-import sessions via the Anthropic API (inherited from the upstream tool)

## Running it

This repo is set up so `ctx` can run the exporter via `uv run` (no global install required).

```bash
uv run --project /path/to/ai-code-sessions ai-code-sessions --help
```

If you use `ctx`, it’s easiest to set this once (see `docs/ctx.md`):

```bash
export CTX_TRANSCRIPTS_PROJECT="/path/to/ai-code-sessions"
```

### Note on examples

If you don’t have `ai-code-sessions` installed on your `PATH`, prefix commands like this:

```bash
uv run --project "$CTX_TRANSCRIPTS_PROJECT" ai-code-sessions <command> ...
```

## Commands

### `json`

Convert a specific JSON or JSONL file to HTML.

Works with:

- Codex rollout JSONL
- Claude Code JSONL
- Claude web-export JSON

```bash
ai-code-sessions json /path/to/session.jsonl -o ./out --open
ai-code-sessions json /path/to/session.jsonl -o ./out --label "My session name"
ai-code-sessions json https://example.com/session.jsonl -o ./out
```

Options:

- `-o/--output`: output directory
- `-a/--output-auto`: create output subdirectory named after input stem
- `--label`: label shown in the transcript header
- `--json`: copy the input JSON/JSONL file into the output directory
- `--repo owner/name`: enables GitHub commit links in the transcript if commit output is detected
- `--open`: open `index.html` after generating
- `--gist`: publish HTML pages to a GitHub Gist (see `docs/privacy.md`)

### `find-source`

Given a time window + working directory, find the most likely native JSONL source file.

```bash
ai-code-sessions find-source \
  --tool codex \
  --cwd "$PWD" \
  --project-root "$(git rev-parse --show-toplevel)" \
  --start 2026-01-02T07:25:55.212Z \
  --end 2026-01-02T09:16:57.576Z
```

This prints the chosen file path.

### `export-latest`

This is the command `ctx` uses. It:

1. Selects the best native JSONL source file using `find-source`
2. Writes `source_match.json` into the output directory
3. Generates `index.html` + `page-*.html`
4. Optionally copies the source JSONL file into the output dir (`--json`)
5. Optionally appends a changelog entry (`--changelog`, see `docs/changelog.md`)

Example:

```bash
ai-code-sessions export-latest \
  --tool codex \
  --cwd "$PWD" \
  --project-root "$(git rev-parse --show-toplevel)" \
  --start 2026-01-02T07:25:55.212Z \
  --end 2026-01-02T09:16:57.576Z \
  -o ./.codex/sessions/2026-01-02-0000_My_Session \
  --label "My Session" \
  --json \
  --changelog
```

Changelog options:

- `--changelog/--no-changelog`: enable/disable changelog generation (default can be set via `CTX_CHANGELOG=1` or `AI_CODE_SESSIONS_CHANGELOG=1`)
- `--changelog-actor`: override `actor` (and therefore the output file under `.changelog/<actor>/entries.jsonl`)
- `--changelog-model`: override Codex model used for changelog generation

### Claude-only commands (inherited)

These commands are inherited from Simon’s tool and are still present:

- `local` (default) — interactive picker from `~/.claude/projects`
- `web` — fetch sessions via the Claude API (requires credentials)
- `all` — build a browsable archive for all local Claude sessions

### `changelog backfill`

Generate `.changelog/<actor>/entries.jsonl` entries from existing `ctx` output directories:

```bash
ai-code-sessions changelog backfill --project-root "$(git rev-parse --show-toplevel)" --actor "your-github-username"
ai-code-sessions changelog backfill --sessions-dir ./.codex/sessions --actor "your-github-username"
```

## What the HTML includes

The transcript includes:

- User + assistant messages
- Tool calls and outputs (including diffs / patch blocks when present)
- “Thinking” blocks (where available)

For Codex, we map:

- `function_call` / `custom_tool_call` → tool-use blocks
- `function_call_output` → tool-result blocks
- `reasoning.summary` → thinking blocks
