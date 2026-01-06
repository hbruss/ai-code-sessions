# The `ais ctx` Workflow

`ais ctx` is the recommended way to use `ai-code-sessions`. It runs Codex or Claude **normally** (preserving all terminal colors and interactivity), then automatically exports a browsable HTML transcript when you exit.

## Why Use `ais ctx`?

Without `ais ctx`, you would need to:

1. Run your AI tool (Codex or Claude)
2. Note the start time
3. Work on your task
4. Note the end time
5. Manually run `ais export-latest` with the correct timestamps
6. Hope you got the right log file

With `ais ctx`, all of this happens automatically:

```bash
# Just add a label and the tool flag
ais ctx "Fix the checkout bug" --codex
# ... work normally ...
# Press Ctrl+D or type /exit
# Transcript appears in .codex/sessions/
```

---

## Installation

```bash
pipx install ai-code-sessions
pipx ensurepath
```

---

## Basic Usage

### Starting a New Session

```bash
# Start a Codex session
ais ctx "Fix the checkout race condition" --codex

# Start a Claude session
ais ctx "Add comprehensive test coverage" --claude
```

The quoted string becomes:
- The **session label** (shown in the transcript header)
- Part of the **directory name** (sanitized for filesystem safety)

### What Happens

1. `ais ctx` captures the current timestamp
2. Launches Codex or Claude with any extra arguments you provide
3. You work normally (full colors, interactivity, everything works)
4. When you exit (Ctrl+D or `/exit`), it captures the end timestamp
5. Finds the matching native log file
6. Generates `index.html` + `page-*.html` transcripts
7. Copies the source JSONL for archival
8. Optionally generates a changelog entry

---

## Resuming Sessions

When you resume a session, `ais ctx` updates the existing transcript directory instead of creating a new one.

### Codex Resume

```bash
# Resume a recent Codex session (behavior depends on CLI version)
ais ctx "Continue checkout fix" --codex resume

# Resume a specific session by ID
ais ctx "Continue checkout fix" --codex resume 01abc234-5678-def0-1234-56789abcdef0
```

> **Tip:** For reliable resume behavior, use `ais resume codex` to pick from a list of prior sessions.

### Claude Resume

```bash
# Resume a specific session (recommended)
ais ctx "Continue test coverage" --claude --resume 01abc234-5678-def0-1234-56789abcdef0
```

### How Resume Works

When you resume:

1. `ais ctx` looks for an existing session directory with a matching label
2. If a session ID is provided, it also matches against `source_match.json`
3. The transcript is regenerated to include both old and new content
4. `export_runs.jsonl` tracks each export window for delta-aware backfills

---

## Interactive Resume Picker

If you don't want to hunt for session IDs, use the picker:

```bash
ais resume codex      # alias: ais ctx-resume codex
ais resume claude     # alias: ais ctx-resume claude
```

The picker is searchable and shows the session label, timestamp (your configured `ctx.tz`), and quick stats.

> **Note:** `ais resume` is **repo-scoped**—it searches for sessions that match the current project root and uses the sanitized label plus session metadata to locate prior exports.

---

## Passing Arguments

Any arguments after `--codex` or `--claude` are forwarded to the underlying tool. Model names must be supported by the selected CLI:

```bash
# Pass Codex arguments
ais ctx "My session" --codex --model gpt-5.1-codex-mini
ais ctx "My session" --codex --quiet

# Pass Claude arguments
ais ctx "My session" --claude --model opus
ais ctx "My session" --claude --no-auto-compact
```

---

## Output Directory Structure

### Codex Sessions

```
<repo-root>/.codex/sessions/2026-01-02-1435_Fix_Checkout_Bug/
├── index.html           # Timeline of all prompts
├── page-001.html        # First 5 conversations
├── page-002.html        # Next 5 conversations (if needed)
├── rollout-abc123.jsonl # Copy of the original log
├── source_match.json    # How the log file was selected
└── export_runs.jsonl    # Export metadata
```

### Claude Sessions

```
<repo-root>/.claude/sessions/2026-01-02-1435_Add_Tests/
├── index.html
├── page-001.html
├── abc123-def4-5678.jsonl
├── source_match.json
└── export_runs.jsonl
```

### Directory Name Format

The directory name follows this pattern:

```
<STAMP>_<SANITIZED_LABEL>[_N]
```

Where:
- `STAMP` = `YYYY-MM-DD-HHMM` in your configured timezone
- `SANITIZED_LABEL` = Your label with spaces replaced by underscores, special characters removed
- `_N` = Disambiguation suffix if a directory with this name exists

**Examples:**

| Label | Directory Name |
|-------|----------------|
| "Fix login bug" | `2026-01-02-1435_Fix_login_bug/` |
| "Add OAuth 2.0 support" | `2026-01-02-1435_Add_OAuth_20_support/` |
| "Debug!!!" | `2026-01-02-1435_Debug/` |

---

## Generated Files Explained

### `index.html`

The main entry point. Contains:
- Session metadata (label, start time, duration)
- A timeline of all prompts with:
  - Preview of each prompt
  - Tool call counts (Bash, Edit, Write, etc.)
  - Commit hashes (linked to GitHub if configured)
  - Pass/fail indicators for test runs
- Links to paginated conversation pages

### `page-*.html`

Each page contains up to 5 full conversations (prompt + response + tool calls). Pagination keeps individual pages fast to load even for long sessions.

### `source_match.json`

Explains how the source log file was selected:

```json
{
  "best": {
    "path": "$HOME/.codex/sessions/2026/01/02/rollout-abc123.jsonl",
    "score": 0.5,
    "cwd_match": true,
    "start_ts": "2026-01-02T14:35:00.123Z",
    "end_ts": "2026-01-02T16:22:45.678Z"
  },
  "candidates": [
    // Up to 25 other files that were considered
  ]
}
```

This is invaluable for debugging if the wrong transcript appears.

### `export_runs.jsonl`

Tracks each export run for this session directory:

```jsonl
{"start": "2026-01-02T14:35:00Z", "end": "2026-01-02T16:22:00Z", "exported_at": "2026-01-02T16:22:05Z"}
{"start": "2026-01-03T09:00:00Z", "end": "2026-01-03T11:30:00Z", "exported_at": "2026-01-03T11:30:10Z"}
```

This enables:
- Delta-only changelog entries for resumed sessions
- Tracking how the transcript has grown over time

---

## Configuration

### Setup Wizard

Run the interactive wizard to configure all options:

```bash
ais setup
```

### Config File

Create `.ai-code-sessions.toml` in your project root:

```toml
[ctx]
tz = "America/Los_Angeles"    # Timezone for folder names

[changelog]
enabled = true                 # Auto-generate changelog entries
actor = "your-github-username" # Who gets credited
evaluator = "codex"           # "codex" or "claude"
```

### Environment Variables

For quick overrides or CI/CD:

| Variable | Description | Example |
|----------|-------------|---------|
| `CTX_TZ` | Timezone for folder names | `America/New_York` |
| `CTX_CODEX_CMD` | Override Codex executable | `/usr/local/bin/codex` |
| `CTX_CLAUDE_CMD` | Override Claude executable | `/usr/local/bin/claude` |
| `CTX_CHANGELOG` | Enable changelog | `1` or `true` |
| `CTX_ACTOR` | Changelog actor | `your-username` |

---

## Tips and Best Practices

### Write Descriptive Labels

Labels appear in:
- The transcript header
- Directory names
- Changelog entries

Good labels make sessions easy to find later:

```bash
# Good: Specific and descriptive
ais ctx "Fix race condition in CartService.checkout()" --codex
ais ctx "Add unit tests for OAuth token refresh" --claude
ais ctx "Refactor database connection pooling for MySQL 8" --codex

# Bad: Vague and unhelpful
ais ctx "fix bug" --codex
ais ctx "work" --claude
ais ctx "stuff" --codex
```

### Use Resume for Multi-Session Tasks

For tasks that span multiple sessions:

```bash
# Day 1: Start the migration
ais ctx "Migrate from REST to GraphQL" --codex
# ... work, then exit

# Day 2: Continue
ais ctx "Continue GraphQL migration" --codex resume
# The transcript includes both sessions

# Day 3: Keep going
ais ctx "Finish GraphQL migration" --codex resume
# All three sessions in one transcript
```

### Add a Shell Alias

If you find yourself typing `ais ctx` frequently:

```bash
# Add to ~/.bashrc or ~/.zshrc
alias ctx='ais ctx'
```

Then:

```bash
ctx "Fix the bug" --codex
ctx "Add tests" --claude
```

### Ignore Session Artifacts in Git

Add to your `.gitignore`:

```gitignore
# AI session artifacts (transcripts contain full code context)
.codex/sessions/
.claude/sessions/
.changelog/
```

The setup wizard (`ais setup`) can do this for you.

---

## Common Issues

### "No matching Codex rollout files found"

Codex isn't writing session logs, or they're in an unexpected location.

```bash
# Check if logs exist (default location)
ls -la ~/.codex/sessions/

# If you have CODEX_HOME set, check there instead
ls -la "$CODEX_HOME/sessions/"

# Try exporting a known file directly
ais json ~/.codex/sessions/2026/01/02/rollout-abc.jsonl -o ./test --open
```

> **Hint:** Codex logs may live under `$CODEX_HOME` instead of `~/.codex` if that environment variable is set. `ais ctx` honors `CODEX_HOME` when searching for logs.

### "Transcript picked the wrong session"

When running concurrent sessions, the matching algorithm occasionally picks the wrong one.

```bash
# Check what was selected
cat .codex/sessions/*/source_match.json | jq .best.path

# Re-export with the correct file
ais json /correct/path/to/rollout.jsonl -o .codex/sessions/my-session --json
```

### "Terminal colors not working"

This shouldn't happen—`ais ctx` runs the underlying tool directly, not through a PTY. If you're seeing issues:

1. Make sure you're using `ais ctx`, not some older PTY-based approach
2. Check that your terminal emulator supports colors
3. File an issue with details about your environment

See [troubleshooting.md](troubleshooting.md) for more solutions.
