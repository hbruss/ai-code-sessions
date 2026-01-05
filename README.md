# ai-code-sessions

**Transform ephemeral AI coding sessions into permanent, browsable artifacts.**

---

## Table of Contents

- [Standing on Simon's Shoulders](#standing-on-simons-shoulders)
- [The Problem Worth Solving](#the-problem-worth-solving)
- [What You Get](#what-you-get)
- [Quick Start](#quick-start)
- [The `ais ctx` Workflow](#the-ais-ctx-workflow)
- [The Changelog System](#the-changelog-system)
- [CLI Reference](#cli-reference)
- [Configuration](#configuration)
- [How Source Matching Works](#how-source-matching-works)
- [Architecture](#architecture)
- [Documentation](#documentation)

---

## Standing on Simon's Shoulders

This project is a fork of [Simon Willison's](https://simonwillison.net/) `claude-code-transcripts` (Apache-2.0). The core of what makes this tool useful—the parsing logic, the paginated HTML rendering, the thoughtful presentation of tool calls and their outputs, the collapsible sections, the clean typography—**that's all Simon's work**.

I discovered his project through his [blog post](https://simonwillison.net/2025/Dec/25/claude-code-transcripts/) and immediately recognized it as the solution to something I'd been wanting: a way to preserve AI coding sessions as readable artifacts. His code does the hard work of transforming messy JSONL logs into something you'd actually want to read.

What I've added on top:

- **Codex CLI support** (in addition to Claude Code)
- **Automatic source matching** for finding the right log file when running concurrent sessions
- **An `ais ctx` workflow** for naming sessions and organizing exports by project
- **An append-only changelog system** for generating structured summaries
- **An interactive setup wizard** for easy configuration

But the rendering engine—the part that makes the HTML output look good—that's Simon's contribution. If you find the transcripts beautiful and readable, credit goes to him. My additions are plumbing around the edges.

The original project: [github.com/simonw/claude-code-transcripts](https://github.com/simonw/claude-code-transcripts)

---

## The Problem Worth Solving

Every time you pair-program with an AI, a complete record of problem-solving unfolds—hypotheses formed, dead ends explored, solutions discovered. But when the terminal closes, that knowledge evaporates.

AI coding tools generate verbose, machine-formatted logs:

- **Codex**: `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`
- **Claude Code**: `~/.claude/projects/<encoded-path>/<session-id>.jsonl`

These files are technically readable, but practically useless for humans. Thousands of lines of JSON. Tool calls nested in content blocks nested in messages. Simon's project transforms that chaos into something you'd actually want to read.

This fork extends his work to support both Codex and Claude, with some workflow conveniences for people who use both.

## What You Get

Each export produces a self-contained directory:

```
.codex/sessions/2026-01-02-1435_fix-auth-race-condition/
├── index.html          # Timeline of prompts with statistics
├── page-001.html       # First 5 conversations
├── page-002.html       # Next 5 conversations
├── source_match.json   # How the source file was identified
└── session.jsonl       # Original log (archived)
```

Simon's rendering engine produces clean, readable HTML:

- **User prompts** appear cleanly, with markdown formatting preserved
- **Assistant responses** show text, tool calls, and reasoning blocks
- **Tool results** are syntax-highlighted and collapsible
- **File edits** display as side-by-side diffs
- **Long content** truncates gracefully with expand buttons
- **Git commits** auto-link to GitHub when detected

The index page shows a timeline of every prompt in the session, with statistics: which tools were called, how many commits were made, whether tests passed. All of this presentation logic comes from the original `claude-code-transcripts`.

---

## Quick Start

### 1. Install

```bash
# Install the CLI globally
pipx install ai-code-sessions
pipx ensurepath

# Verify it works
ais --help
```

### 2. Run the Setup Wizard (Recommended)

```bash
ais setup
```

The wizard will:
- Ask for your GitHub username (for changelog attribution)
- Set your preferred timezone for session folder names
- Configure changelog generation preferences
- Optionally update your `.gitignore`

### 3. Start Your First Session

```bash
# With Codex
ais ctx "Fix the login race condition" --codex

# With Claude Code
ais ctx "Add unit tests for auth module" --claude
```

When you exit the AI tool (Ctrl+D or `/exit`), your session is automatically exported to:
- `.codex/sessions/YYYY-MM-DD-HHMM_Your_Label/` (for Codex)
- `.claude/sessions/YYYY-MM-DD-HHMM_Your_Label/` (for Claude)

### 4. View Your Transcript

```bash
# macOS
open .codex/sessions/*/index.html

# Linux
xdg-open .codex/sessions/*/index.html
```

---

## The `ais ctx` Workflow

`ais ctx` is the recommended way to use this tool. It wraps the Codex or Claude CLI, preserving all terminal colors and interactivity, and automatically exports a transcript when you're done.

### Basic Usage

```bash
# Start a new Codex session with a descriptive label
ais ctx "Refactor database connection pool" --codex

# Start a new Claude session
ais ctx "Debug memory leak in worker process" --claude
```

### Resuming Sessions

Sessions can be resumed, and `ais ctx` will update the existing transcript:

```bash
# Resume the most recent Codex session
ais ctx "Continue database refactor" --codex resume

# Resume a specific Codex session by ID
ais ctx "Continue database refactor" --codex resume abc123

# Resume a specific Claude session
ais ctx "Continue memory debugging" --claude --resume abc123

# Or pick from a list
ais resume codex
ais resume claude
```

### What Gets Generated

After each session, you'll find:

| File | Description |
|------|-------------|
| `index.html` | Timeline of all prompts with tool call statistics |
| `page-001.html`, `page-002.html`, ... | Paginated conversation pages (5 conversations each) |
| `source_match.json` | Metadata about which native log file was selected |
| `rollout-*.jsonl` or `*.jsonl` | Copy of the original session log |
| `export_runs.jsonl` | Export metadata (for resumable backfills) |

### Tips

- **Labels are important**: They become part of the directory name and appear in transcripts
- **Use descriptive labels**: "Fix login bug" is better than "debug"
- **Sessions are per-repo**: Transcripts are stored in your project directory, making them easy to find

---

## The Changelog System

Beyond transcripts, `ai-code-sessions` can generate structured changelog entries after each session. These aren't commit messages—they're higher-level summaries of what an AI-assisted coding session actually accomplished.

### What Gets Captured

Each entry includes:

| Field | Description |
|-------|-------------|
| `summary` | One-line description of the session's purpose |
| `bullets` | 3-5 specific changes or accomplishments |
| `tags` | Classification (`feat`, `fix`, `refactor`, `docs`, etc.) |
| `touched_files` | Created/modified/deleted/moved files (best-effort) |
| `tests` | Test commands + results (`pass`/`fail`/`unknown`) |
| `commits` | Git commits made during the session |

### Where Changelogs Live

```
.changelog/
└── your-username/
    ├── entries.jsonl    # Successful changelog entries (append-only)
    └── failures.jsonl   # Failed generation attempts (for debugging)
```

### Enabling Changelog Generation

**Option 1: Environment Variables**

```bash
export CTX_ACTOR="your-github-username"
export CTX_CHANGELOG=1
```

**Option 2: Setup Wizard**

```bash
ais setup
```

**Option 3: Per-Repo Config**

Create `.ai-code-sessions.toml` in your project root:

```toml
[changelog]
enabled = true
actor = "your-github-username"
```

### Choosing an Evaluator

Changelog entries are generated by an AI evaluator. You can choose:

| Evaluator | Model | Strengths |
|-----------|-------|-----------|
| `codex` (default) | Default: `gpt-5.2` with `xhigh` reasoning | Fast, good at summarization |
| `claude` | Default: `opus` with 8192 thinking tokens | More detailed analysis |

Model names must be supported by the selected CLI. Codex CLI documents its model list (for example, `gpt-5.2-codex` and `gpt-5.1-codex-mini`) and accepts `--model`/`-m` overrides.

Configure via environment:

```bash
export CTX_CHANGELOG_EVALUATOR="claude"
export CTX_CHANGELOG_MODEL="opus"
```

Or in config:

```toml
[changelog]
evaluator = "claude"
model = "opus"
claude_thinking_tokens = 8192
```

### Backfilling Existing Sessions

Generate changelog entries for sessions that were exported before you enabled changelogs:

```bash
# Backfill all sessions in current repo
ais changelog backfill --project-root "$(git rev-parse --show-toplevel)"

# Backfill a specific sessions directory
ais changelog backfill --sessions-dir ./.codex/sessions

# Use Claude as the evaluator with custom concurrency
ais changelog backfill --evaluator claude --max-concurrency 5
```

### Privacy Note

Consider adding `.changelog/` to your `.gitignore` if you don't want to commit these entries (recommended for public repos).

---

## CLI Reference

All commands are available via `ais` (short) or `ai-code-sessions` (full).

### `ais setup`

Interactive wizard to configure global and per-repo settings.

```bash
ais setup
ais setup --no-global        # Skip global config
ais setup --no-repo          # Skip per-repo config
ais setup --force            # Overwrite existing configs
```

### `ais ctx`

Start a labeled AI coding session with automatic transcript export.

```bash
ais ctx "My session label" --codex
ais ctx "My session label" --claude
ais ctx "My session label" --codex resume
ais ctx "My session label" --claude --resume <session-id>
ais resume codex
```

### `ais json`

Convert a specific JSON/JSONL file to HTML transcript.

```bash
# Basic conversion
ais json /path/to/session.jsonl -o ./out

# With options
ais json /path/to/session.jsonl \
  -o ./out \
  --label "My Session Name" \
  --json \
  --open \
  --repo owner/name
```

| Option | Description |
|--------|-------------|
| `-o, --output` | Output directory (required) |
| `--label` | Label shown in transcript header |
| `--json` | Copy input file to output directory |
| `--repo` | Enable GitHub commit links (`owner/repo`) |
| `--open` | Open `index.html` after generating |
| `--gist` | Upload to GitHub Gist |

### `ais find-source`

Find the native log file matching a time window (used internally by `ais ctx`).

```bash
ais find-source \
  --tool codex \
  --cwd "$PWD" \
  --project-root "$(git rev-parse --show-toplevel)" \
  --start 2026-01-02T07:25:55.212Z \
  --end 2026-01-02T09:16:57.576Z
```

### `ais export-latest`

Export the most recent session matching a time window (used internally by `ais ctx`).

```bash
ais export-latest \
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

### `ais changelog backfill`

Generate changelog entries for existing session directories.

```bash
ais changelog backfill --project-root "$(git rev-parse --show-toplevel)"
ais changelog backfill --sessions-dir ./.codex/sessions --actor "username"
ais changelog backfill --evaluator claude --max-concurrency 5
```

| Option | Description |
|--------|-------------|
| `--project-root` | Git repo containing session outputs |
| `--sessions-dir` | Specific sessions directory to process |
| `--actor` | Override changelog actor |
| `--evaluator` | `codex` or `claude` (default: `codex`) |
| `--model` | Model override for evaluator |
| `--max-concurrency` | Max concurrent evaluations (Claude only, default: 5) |

### Claude-Specific Commands (Inherited)

These commands are inherited from Simon's original tool:

- `ais local` — Interactive picker from `~/.claude/projects`
- `ais web` — Fetch sessions via the Claude API
- `ais all` — Build browsable archive for all local Claude sessions

---

## Configuration

### Config File Locations

| Type | Location |
|------|----------|
| Global (macOS) | `~/Library/Application Support/ai-code-sessions/config.toml` |
| Global (Linux) | `~/.config/ai-code-sessions/config.toml` |
| Global (Windows) | `%APPDATA%\ai-code-sessions\config.toml` |
| Per-repo | `.ai-code-sessions.toml` or `.ais.toml` in project root |

### Precedence (Highest to Lowest)

1. CLI flags
2. Environment variables
3. Per-repo config
4. Global config

### Example Config

```toml
[ctx]
tz = "America/Los_Angeles"    # Timezone for session folder names

[changelog]
enabled = true                 # Enable changelog generation
actor = "your-github-username" # Who gets credited in changelogs
evaluator = "codex"           # "codex" or "claude"
model = ""                     # Blank uses tool defaults
claude_thinking_tokens = 8192  # Claude-specific setting
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `CTX_TZ` | Timezone for session folder names |
| `CTX_CODEX_CMD` | Override Codex executable path |
| `CTX_CLAUDE_CMD` | Override Claude executable path |
| `CTX_CHANGELOG` | Enable changelog (`1`/`true`) |
| `CTX_ACTOR` | Changelog actor (username) |
| `CTX_CHANGELOG_EVALUATOR` | `codex` or `claude` |
| `CTX_CHANGELOG_MODEL` | Model for evaluator |
| `CTX_CHANGELOG_CLAUDE_THINKING_TOKENS` | Claude thinking tokens |

---

## How Source Matching Works

When you run concurrent AI sessions, identifying which log file belongs to which session becomes non-trivial. This project solves it with intelligent matching:

1. **Date search**: Scans session directories for the date range ±1 day
2. **Modification time filter**: Considers files modified within the session window
3. **Timestamp extraction**: Reads first/last entries to get actual session bounds
4. **Working directory matching**: Verifies the session was started in the expected directory
5. **Scoring**: Minimizes timestamp delta to find the best match

The result is saved to `source_match.json` with the selected file and up to 25 candidates—so you can verify or manually override if needed.

### Debugging Source Matching

If a transcript picks the wrong session:

```bash
# Check what was selected
cat .codex/sessions/*/source_match.json | jq .best

# Re-export with the correct file
ais json /path/to/correct-file.jsonl -o .codex/sessions/my-session --json
```

---

## Architecture

The implementation lives in a single focused module (`src/ai_code_sessions/__init__.py`, ~5,000 lines) with Jinja2 templates for HTML rendering. Key patterns:

- **Format normalization**: Both Codex and Claude logs are parsed into a common "loglines" format
- **Content block handling**: Modern multi-block messages (text + images + tool calls) are rendered correctly
- **Graceful degradation**: Export succeeds even if changelog generation fails
- **Content-addressed deduplication**: Changelog entries have content-based IDs to prevent duplicates
- **Parallel backfill**: Claude changelog backfill runs up to 5 evaluations concurrently

---

## Documentation

Detailed documentation is available in the `docs/` directory:

| Document | Description |
|----------|-------------|
| [docs/README.md](docs/README.md) | Documentation overview |
| [docs/cli.md](docs/cli.md) | Complete CLI reference |
| [docs/ctx.md](docs/ctx.md) | The `ais ctx` workflow |
| [docs/config.md](docs/config.md) | Configuration options |
| [docs/changelog.md](docs/changelog.md) | Changelog generation |
| [docs/source-matching.md](docs/source-matching.md) | How source file matching works |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Common issues and fixes |
| [docs/privacy.md](docs/privacy.md) | Privacy and safety considerations |
| [docs/development.md](docs/development.md) | Contributing and development |
| [docs/pypi.md](docs/pypi.md) | Publishing to PyPI |

---

*Because every debugging session teaches something worth remembering.*
