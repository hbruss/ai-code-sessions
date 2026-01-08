# CLI Reference

The `ai-code-sessions` CLI converts native AI session logs into clean, mobile-friendly HTML transcripts with pagination.

## Command Names

You can use any of these names to invoke the CLI:

| Command | Description |
|---------|-------------|
| `ais` | Short alias (recommended) |
| `ai-code-sessions` | Full name |
| `ai-code-transcripts` | Legacy alias (for compatibility) |

## Supported Log Formats

The CLI can process logs from:

- **Codex CLI** — `~/.codex/sessions/**/rollout-*.jsonl` (or `$CODEX_HOME/sessions/...` if `CODEX_HOME` is set)
- **Claude Code** — `~/.claude/projects/**/<uuid>.jsonl`
- **Claude Web Export** — JSON files exported from claude.ai

---

## Global Options

These flags can be used with any command:

| Option | Description |
|--------|-------------|
| `--version` | Show CLI version |
| `-v, --verbose` | Increase log verbosity (repeatable) |
| `--log-file PATH` | Write logs to a file (or set `AI_CODE_SESSIONS_LOG_DIR`) |

---

## Installation

### Using pipx (Recommended)

```bash
# Install globally in an isolated environment
pipx install ai-code-sessions
pipx ensurepath

# Verify installation
ais --help
```

### For Development

If you're hacking on this repo:

```bash
# Clone the repo
git clone https://github.com/hbruss/ai-code-sessions.git
cd ai-code-sessions

# Run with uv
uv run --project . ai-code-sessions --help
```

---

## Commands

### `ais setup`

Run an interactive setup wizard to configure global and per-repo settings.

```bash
ais setup
```

**What it does:**

1. Asks for your GitHub username (for changelog attribution)
2. Sets your preferred timezone for session folder names
3. Configures changelog generation preferences
4. Optionally updates your `.gitignore`

**Options:**

| Option | Description |
|--------|-------------|
| `--project-root PATH` | Target git repo (defaults to current repo) |
| `--global / --no-global` | Write global config (default: yes) |
| `--repo / --no-repo` | Write per-repo config (default: yes) |
| `--force` | Overwrite existing config files |

**Examples:**

```bash
# Run full setup wizard
ais setup

# Only update global config
ais setup --no-repo

# Only update repo config
ais setup --no-global

# Force overwrite existing configs
ais setup --force
```

---

### `ais ctx`

Start a labeled AI coding session with automatic transcript export when you finish.

**This is the recommended way to use this tool.** It wraps Codex or Claude, preserving all terminal colors and interactivity.

```bash
ais ctx "Your descriptive label" --codex
ais ctx "Your descriptive label" --claude
```

**Examples:**

```bash
# Start a new Codex session
ais ctx "Fix authentication race condition" --codex

# Start a new Claude session
ais ctx "Add comprehensive test coverage" --claude

# Resume the most recent Codex session
ais ctx "Continue auth fix" --codex resume

# Resume a specific Codex session by ID
ais ctx "Continue auth fix" --codex resume abc123def

# Resume a Claude session
ais ctx "Continue testing" --claude --resume <session-id>

# Resume a specific Claude session
ais ctx "Continue testing" --claude --resume abc123def
```

**Output directory:**

Sessions are saved to your project repo:

- Codex: `<repo>/.codex/sessions/<TIMESTAMP>_<LABEL>/`
- Claude: `<repo>/.claude/sessions/<TIMESTAMP>_<LABEL>/`

**What gets generated:**

| File | Description |
|------|-------------|
| `index.html` | Timeline with prompt summaries and statistics |
| `page-001.html` | First 5 conversations (paginated) |
| `page-002.html` | Next 5 conversations, etc. |
| `source_match.json` | Metadata about which log file was selected |
| `*.jsonl` | Copy of the original session log |
| `export_runs.jsonl` | Export metadata for resumable backfills |

See [ctx.md](ctx.md) for detailed documentation.

---

### `ais resume`

Pick a previous session from the current repo and resume it with a friendly picker.

**Alias:** `ais ctx-resume`

```bash
# Resume a Codex session (interactive list)
ais resume codex

# Resume a Claude session (interactive list)
ais resume claude

# Resume newest session without prompting
ais resume codex --latest
```

**Options:**

| Option | Description |
|--------|-------------|
| `--limit N` | Max sessions to show (default: 50) |
| `--latest` | Resume newest session without prompting |
| `--open` | Open the transcript after export |
| `--changelog / --no-changelog` | Append changelog entry (best-effort) |
| `--changelog-actor TEXT` | Override changelog actor |
| `--changelog-evaluator TEXT` | `codex` or `claude` |
| `--changelog-model TEXT` | Model override |

---

### `ais json`

Convert a specific JSON or JSONL file to an HTML transcript.

Works with:
- Codex rollout JSONL files
- Claude Code JSONL files
- Claude web-export JSON files

**Basic usage:**

```bash
ais json /path/to/session.jsonl -o ./output-dir
```

**Options:**

| Option | Description |
|--------|-------------|
| `-o, --output DIR` | Output directory (required) |
| `-a, --output-auto` | Create subdirectory named after input file |
| `--label TEXT` | Label shown in transcript header |
| `--json` | Copy input file to output directory |
| `--repo OWNER/NAME` | GitHub commit links (auto-detected if not specified) |
| `--open` | Open `index.html` after generating |
| `--gist` | Publish to GitHub Gist |
| `--output-mode` | `merge` (update existing), `overwrite` (replace files), or `clean` (delete dir first) |
| `--prune-pages/--no-prune-pages` | Remove stale `page-*.html` files beyond the new page count |

**When to use each output mode:**
- `merge` (default): Safe for resumed sessions; only updates changed files
- `overwrite`: Replace existing files but keep other files in the directory
- `clean`: Start fresh; use when the old export is corrupt or you want a clean slate

**Examples:**

```bash
# Basic conversion
ais json ~/.codex/sessions/2026/01/02/rollout-abc123.jsonl -o ./my-transcript

# With a custom label
ais json ~/session.jsonl -o ./out --label "Debugging Memory Leak"

# Copy source file and open in browser
ais json ~/session.jsonl -o ./out --json --open

# Enable GitHub commit links
ais json ~/session.jsonl -o ./out --repo myorg/myrepo

# Convert from URL
ais json https://example.com/session.jsonl -o ./out

# Auto-name output directory
ais json ~/rollout-abc123.jsonl -a
# Creates: ./rollout-abc123/index.html
```

---

### `ais find-source`

Find the native log file that matches a given time window and working directory.

This is primarily used internally by `ais ctx`, but can be helpful for debugging.

**Usage:**

```bash
ais find-source \
  --tool codex \
  --cwd "$PWD" \
  --project-root "$(git rev-parse --show-toplevel)" \
  --start 2026-01-02T07:25:55.212Z \
  --end 2026-01-02T09:16:57.576Z
```

**Options:**

| Option | Description |
|--------|-------------|
| `--tool` | `codex` or `claude` (required) |
| `--cwd PATH` | Working directory where session started |
| `--project-root PATH` | Git repository root |
| `--start ISO_TIMESTAMP` | Session start time (UTC) |
| `--end ISO_TIMESTAMP` | Session end time (UTC) |
| `--debug-json PATH` | Write debug info to JSON file |

**Example with debug output:**

```bash
ais find-source \
  --tool codex \
  --cwd /home/user/myproject \
  --project-root /home/user/myproject \
  --start 2026-01-02T14:00:00Z \
  --end 2026-01-02T16:30:00Z \
  --debug-json /tmp/source_debug.json

# Then examine candidates
cat /tmp/source_debug.json | jq '.candidates[:5]'
```

---

### `ais export-latest`

Export the most recent session matching a time window. This is what `ais ctx` calls internally.

**Usage:**

```bash
ais export-latest \
  --tool codex \
  --cwd "$PWD" \
  --project-root "$(git rev-parse --show-toplevel)" \
  --start 2026-01-02T07:25:55.212Z \
  --end 2026-01-02T09:16:57.576Z \
  -o ./.codex/sessions/2026-01-02-0000_My_Session \
  --label "My Session"
```

**Options:**

| Option | Description |
|--------|-------------|
| `--tool` | `codex` or `claude` (required) |
| `--cwd PATH` | Working directory |
| `--project-root PATH` | Git repository root |
| `--start ISO_TIMESTAMP` | Session start time |
| `--end ISO_TIMESTAMP` | Session end time |
| `-o, --output DIR` | Output directory (required) |
| `--label TEXT` | Session label |
| `--json` | Copy source JSONL to output |
| `--open` | Open `index.html` after export |
| `--output-mode` | `merge`, `overwrite`, or `clean` output directories |
| `--prune-pages/--no-prune-pages` | Remove stale `page-*.html` files beyond the new page count |
| `--changelog` | Generate changelog entry |
| `--no-changelog` | Disable changelog |
| `--changelog-actor TEXT` | Override actor for changelog |
| `--changelog-evaluator TEXT` | `codex` or `claude` |
| `--changelog-model TEXT` | Model override |

**Example with changelog:**

```bash
ais export-latest \
  --tool codex \
  --cwd "$PWD" \
  --project-root "$(git rev-parse --show-toplevel)" \
  --start 2026-01-02T14:00:00Z \
  --end 2026-01-02T16:30:00Z \
  -o ./.codex/sessions/2026-01-02-1400_Fix_Auth \
  --label "Fix Authentication" \
  --json \
  --changelog \
  --changelog-actor "myusername"
```

---

### `ais config show`

Show resolved configuration values and their provenance.

```bash
ais config show
ais config show --project-root /path/to/repo
ais config show --json
```

**Options:**

| Option | Description |
|--------|-------------|
| `--project-root PATH` | Target git repo (defaults to current repo) |
| `--json` | Output config + provenance as JSON |

---

### `ais archive`

Generate a repo-level archive that links to existing `.codex/sessions` and `.claude/sessions` outputs.

```bash
ais archive
ais archive --project-root /path/to/repo
ais archive -o ./.ais-archive --open
```

**Options:**

| Option | Description |
|--------|-------------|
| `--project-root PATH` | Target git repo (defaults to current repo) |
| `-o, --output DIR` | Output directory (default: `<project_root>/.ais-archive`) |
| `--open` | Open the archive in your browser |

---

### `ais changelog backfill`

Generate changelog entries for existing session directories that were exported before changelog generation was enabled.

**Usage:**

```bash
ais changelog backfill --project-root "$(git rev-parse --show-toplevel)"
```

**Options:**

| Option | Description |
|--------|-------------|
| `--project-root PATH` | Git repo with session outputs |
| `--sessions-dir PATH` | Specific sessions directory |
| `--actor TEXT` | Changelog actor (username) |
| `--evaluator TEXT` | `codex` or `claude` (default: `codex`) |
| `--model TEXT` | Model override (must be supported by evaluator CLI) |
| `--max-concurrency N` | Max concurrent evaluations (Claude only, default: 5) |
| `--limit N` | Process only N sessions (for testing) |

**Examples:**

```bash
# Backfill all sessions in current repo
ais changelog backfill --project-root "$(git rev-parse --show-toplevel)"

# Backfill a specific sessions directory
ais changelog backfill --sessions-dir ./.codex/sessions --actor "myusername"

# Use Claude as the evaluator
ais changelog backfill --evaluator claude --model opus

# Limit concurrency for rate limiting
ais changelog backfill --evaluator claude --max-concurrency 2

# Process only 5 sessions (useful for testing)
ais changelog backfill --limit 5 --max-concurrency 1
```

**Notes:**

- If `export_runs.jsonl` exists in a session directory, backfill can generate **delta-only** entries for resumed sessions
- Without it, backfill creates a single entry per session directory
- If the evaluator hits rate limits (`HTTP 429`), backfill halts early so you can retry later

---

### `ais changelog since`

Query changelog entries since a specific date or git commit. Useful for reviewing recent work or generating reports.

**Usage:**

```bash
ais changelog since <ref>
```

**REF can be:**

- **ISO date**: `2026-01-06`, `2026-01-06T10:30:00`
- **Relative**: `yesterday`, `today`, `"2 days ago"`, `"last week"`
- **Git ref**: `abc1234`, `HEAD~5`, `main`, `v1.0.0`

**Options:**

| Option | Description |
|--------|-------------|
| `--format FORMAT` | Output format: `summary` (default), `json`, `bullets`, `table` |
| `--project-root PATH` | Git repo root (defaults to current repo) |
| `--actor TEXT` | Filter by actor |
| `--tool TEXT` | Filter by tool (`codex` or `claude`) |
| `--tag TEXT` | Filter by tag (repeatable) |

**Examples:**

```bash
# Show entries since a specific date
ais changelog since 2026-01-06

# Show yesterday's entries
ais changelog since yesterday

# Show entries from the last 3 days
ais changelog since "3 days ago"

# Show entries since a git commit
ais changelog since HEAD~5
ais changelog since abc1234

# Output as JSON for scripting
ais changelog since yesterday --format json

# Output as markdown table
ais changelog since "last week" --format table

# Filter by tool
ais changelog since yesterday --tool codex

# Filter by tag
ais changelog since "2 days ago" --tag feat --tag fix
```

**Output Formats:**

| Format | Description |
|--------|-------------|
| `summary` | One-line summaries (default) |
| `json` | Full JSON entries |
| `bullets` | Markdown with bullets and tags |
| `table` | Markdown table |

---

### `ais changelog lint`

Validate existing changelog entries for quality issues like truncation or Unicode garbage.

**Usage:**

```bash
ais changelog lint
```

**Options:**

| Option | Description |
|--------|-------------|
| `--project-root PATH` | Git repo root (defaults to current repo) |
| `--actor TEXT` | Filter by actor |
| `--fix` | Re-evaluate entries with validation errors and replace them |
| `--evaluator TEXT` | Evaluator to use for `--fix` mode: `codex` (default) or `claude` |
| `--model TEXT` | Model override for the evaluator |
| `-v, --verbose` | Show details for all entries, not just those with issues |
| `--dry-run` | With `--fix`, show what would be fixed without making changes |

**Examples:**

```bash
# Scan all entries for issues
ais changelog lint

# Scan entries for a specific actor
ais changelog lint --actor myusername

# Show all entries (including valid ones)
ais changelog lint --verbose

# Fix entries with issues using the default evaluator (codex)
ais changelog lint --fix

# Preview what would be fixed without making changes
ais changelog lint --fix --dry-run

# Fix using Claude as the evaluator
ais changelog lint --fix --evaluator claude
```

**What it checks:**

- **Truncated content**: Incomplete words/sentences (e.g., ending with lowercase letter)
- **Unicode garbage**: Unexpected characters (e.g., Devanagari from ANSI issues)
- **Empty or very short bullets**: Bullets with fewer than 5 characters
- **Path-only bullets**: Bullets that appear to be just file paths

**Notes:**

- The `--fix` flag creates a backup (`entries.jsonl.bak`) before modifying entries
- Only entries with source transcripts still available can be fixed
- Re-evaluation uses the same digest-based approach as the original changelog generation

---

### `ais changelog refresh-metadata`

Recompute entry metadata (`touched_files`, `tests`, `commits`) from the stored transcript JSONL without re-running the evaluator (Codex/Claude).

This is useful when metadata extraction improves (for example, a parser update starts detecting `apply_patch` file touches that were previously missed) and you want to update historical entries without spending evaluator usage.

**Usage:**

```bash
ais changelog refresh-metadata
```

**Options:**

| Option | Description |
|--------|-------------|
| `--project-root PATH` | Git repo root (defaults to current repo) |
| `--actor TEXT` | Filter by actor |
| `--only-empty/--all` | Only refresh entries with empty `touched_files` (default) or all entries |
| `--dry-run` | Preview what would be refreshed without writing |

**Examples:**

```bash
# Preview what would change (recommended first)
ais changelog refresh-metadata --dry-run

# Refresh metadata for a single actor
ais changelog refresh-metadata --actor myusername

# Force recompute for every entry (not just empty touched_files)
ais changelog refresh-metadata --all
```

**Notes:**

- A backup (`entries.jsonl.bak`) is created before modifying entries
- Entries without an on-disk transcript (`transcript.source_jsonl`) cannot be refreshed

---

### Claude-Specific Commands (Inherited)

These commands are inherited from Simon Willison's original `claude-code-transcripts` tool:

#### `ais local`

Interactive picker for local Claude sessions.

```bash
ais local
ais local -o ./output-dir
ais local --open
```

#### `ais web`

Fetch Claude sessions via the API (requires credentials).

```bash
ais web
ais web <session-id>
ais web -o ./output-dir --open
```

#### `ais all`

Build a browsable archive of all local Claude sessions.

```bash
ais all -o ./archive
ais all --source ~/.claude/projects
```

---

## What the HTML Includes

The generated transcript includes:

- **User messages** — Your prompts with markdown formatting preserved
- **Assistant messages** — AI responses with text, code blocks, and explanations
- **Tool calls** — Bash commands, file edits, web searches, etc.
- **Tool outputs** — Command output, diffs, API responses (syntax-highlighted)
- **Thinking blocks** — AI reasoning (where available)

### GitHub Commit Links

Commits in transcripts can link to GitHub. The repo is auto-detected from:

1. **Codex session metadata** — Git info recorded when the session started (v0.1.3+)
2. **Git push output** — URLs like `github.com/owner/repo/pull/new/branch` in tool results

If auto-detection fails, specify manually with `--repo owner/name`.

For Codex sessions, we map:

| Codex Format | Rendered As |
|--------------|-------------|
| `function_call` / `custom_tool_call` | Tool-use blocks |
| `function_call_output` | Tool-result blocks |
| `reasoning.summary` | Thinking blocks |

---

## Tips and Best Practices

### Label Your Sessions Well

Good labels make transcripts easy to find later:

```bash
# Good labels
ais ctx "Fix checkout race condition in CartService" --codex
ais ctx "Add integration tests for OAuth flow" --claude
ais ctx "Refactor database connection pooling" --codex

# Less helpful labels
ais ctx "debug" --codex
ais ctx "work" --claude
ais ctx "stuff" --codex
```

### Use Resume for Long Tasks

When working on something over multiple sessions:

```bash
# Day 1
ais ctx "Migrate to new API version" --codex
# ... work, then exit

# Day 2: resume the same transcript
ais ctx "Continue API migration" --codex resume
```

### Check Source Matching

If a transcript seems wrong, check `source_match.json`:

```bash
cat .codex/sessions/*/source_match.json | jq .best.path
```

If it picked the wrong file, re-export manually:

```bash
ais json /correct/path/to/rollout-xyz.jsonl \
  -o .codex/sessions/my-session \
  --json
```
