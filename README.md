# ai-code-sessions

**Transform ephemeral AI coding sessions into permanent, browsable artifacts.**

---

## Table of Contents

- [Standing on Simon's Shoulders](#standing-on-simons-shoulders)
- [The Problem Worth Solving](#the-problem-worth-solving)
- [What You Get](#what-you-get)
- [Quick Start](#quick-start)
- [The Normal Workflow](#the-normal-workflow)
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
- **A native changelog sync workflow** for processing recent Codex and Claude sessions without wrapping them
- **An optional `ais ctx` workflow** for naming sessions and organizing transcript exports by project
- **An append-only changelog system** for generating structured summaries
- **An interactive onboarding wizard** for workflow setup, readiness checks, and manual skill-install guidance

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
- **Git commits** auto-link to GitHub (detected from session metadata or git push output)

The index page shows a timeline of every prompt in the session, with statistics: which tools were called, how many commits were made, whether tests passed. All of this presentation logic comes from the original `claude-code-transcripts`.

---

## Quick Start

### 1. Install On macOS (Apple Silicon)

Open Terminal and run:

```bash
# Install pipx if needed
python3 -m pip install --user pipx
python3 -m pipx ensurepath
export PATH="$HOME/.local/bin:$PATH"

# Install ai-code-sessions
python3 -m pipx install ai-code-sessions

# Recommended helper tools for the shipped changelog skill
brew install jq ripgrep

# Verify the install
ais --version
ais --help
```

### 2. Run the Onboarding Wizard (Recommended)

```bash
ais setup
```

The wizard will:
- Ask which CLI(s) `ais ctx` should wrap
- Ask which CLI should generate changelog entries
- Check readiness for the selected workflow (`codex`, `claude`) and warn about helper-tool support (`jq`, `rg`, and optional helpers)
- Ask whether config should be written globally, per-repo, or both
- Print exact manual skill-install commands for Codex and/or Claude

### 3. Install The Shipped Changelog Skill (Optional, Manual)

Find the packaged bundle path:

```bash
ais skill path changelog
```

Install it user-wide for Codex:

```bash
mkdir -p ~/.codex/skills/changelog
cp -R "$(ais skill path changelog)"/. ~/.codex/skills/changelog/
test -f ~/.codex/skills/changelog/SKILL.md
```

Install it user-wide for Claude Code:

```bash
mkdir -p ~/.claude/skills/changelog
cp -R "$(ais skill path changelog)"/. ~/.claude/skills/changelog/
test -f ~/.claude/skills/changelog/SKILL.md
```

Detailed instructions for Codex user-wide, Codex project-local, Claude user-wide, Claude project-local, and Windows PowerShell installs live in [`docs/skills.md`](docs/skills.md).

### 4. Run Your Session Normally

```bash
# Use Codex or Claude directly
codex
claude
```

### 5. Sync the Changelog

```bash
# Sync recent Codex sessions from the last 48 hours
ais changelog sync --codex

# Preview Claude sessions without writing
ais changelog sync --claude --dry-run
```

If repo targeting is ambiguous, `ais` prompts you to choose the correct project before it writes anything.

### 6. Export HTML When You Want It

```bash
# Optional convenience wrapper for automatic transcript export
ais ctx "Fix the login race condition" --codex
```

---

## The Normal Workflow

`ais changelog sync` is the primary workflow for this tool now. Use Codex or Claude normally, then let `ais` scan recent native session logs, resolve the correct repo, and append changelog entries only for sessions that have not already been recorded.

### Basic Usage

```bash
# Default: scan the last 48 hours
ais changelog sync --codex

# Scan both tools over a larger window
ais changelog sync --all --since "7 days ago"

# Preview actions without writing
ais changelog sync --claude --dry-run
```

### What It Does

- Discovers recent native Codex and Claude sessions
- Resolves the target git repo from session evidence
- Prompts you when multiple repos are plausible
- Reports ambiguous sessions as unresolved in non-interactive runs
- Skips low-confidence sessions instead of guessing
- Appends changelog entries only for sessions that are not already recorded

Use [`docs/changelog.md`](docs/changelog.md) for the full changelog workflow and [`docs/cli.md`](docs/cli.md) for the complete flag reference.

---

## The `ais ctx` Workflow

`ais ctx` is now the optional convenience workflow. Use it when you want automatic HTML transcript export, session labels at launch time, and managed resume behavior in the repo. If you only want changelog entries, you can skip the wrapper and use `ais changelog sync` after the session ends.

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

`ai-code-sessions` can generate structured changelog entries directly from recent native sessions or from exported transcript directories. These aren't commit messages; they're higher-level summaries of what an AI-assisted coding session actually accomplished.

### Normal Usage

```bash
# Default: recent sessions from the last 48 hours
ais changelog sync --codex

# Sync both tools over a custom window
ais changelog sync --all --since "7 days ago"

# Restrict writes to one repo and preview the result
ais changelog sync --claude --project-root "$(git rev-parse --show-toplevel)" --dry-run
```

`ais changelog sync` is idempotent. You can run it after every session; it skips sessions that are already represented in the target repo's changelog.

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

**Note:** When using the `claude` evaluator, `ai-code-sessions` runs Claude Code in headless mode with MCP servers disabled (via `--strict-mcp-config` and an empty `--mcp-config`) to keep evaluation fast and deterministic.

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

Generate changelog entries for historical transcript directories that were exported before you started using `ais changelog sync` or before changelog generation was enabled:

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

Interactive onboarding wizard for workflow choices, readiness checks, config scope, and manual skill-install guidance.

```bash
ais setup
ais setup --no-global        # Skip global config
ais setup --no-repo          # Skip per-repo config
ais setup --force            # Overwrite existing configs
```

### `ais skill path changelog`

Print the packaged path for the shipped changelog skill bundle.

```bash
ais skill path changelog
```

### `ais ctx`

Optional convenience wrapper for a labeled AI coding session with automatic transcript export.

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

### `ais changelog sync`

Sync recent native Codex or Claude sessions into the correct repo changelog.

```bash
ais changelog sync --codex
ais changelog sync --all --since "7 days ago"
ais changelog sync --claude --project-root "$(git rev-parse --show-toplevel)" --dry-run
```

| Option | Description |
|--------|-------------|
| `--codex`, `--claude`, `--all` | Select which native session stores to scan |
| `--since`, `--until` | Define the scan window (`--since` defaults to 48 hours before `--until`/now) |
| `--limit` | Limit the number of discovered sessions considered |
| `--project-root` | Restrict writes to one repo; matching medium-confidence sessions do not prompt |
| `--dry-run` | Show planned appends without writing |
| `--actor` | Override changelog actor |
| `--evaluator` | `codex` or `claude` (default: `codex`) |
| `--model` | Model override for the evaluator |

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

### `ais changelog since`

Query changelog entries by date or git commit.

```bash
ais changelog since 2026-01-06              # Since a specific date
ais changelog since yesterday               # Since yesterday
ais changelog since "3 days ago"            # Relative date
ais changelog since HEAD~5                  # Since a git commit
ais changelog since main --format json      # Output as JSON
ais changelog since yesterday --tool codex  # Filter by tool
```

| Option | Description |
|--------|-------------|
| `--format` | Output format: `summary`, `json`, `bullets`, `table` |
| `--project-root` | Git repo root |
| `--actor` | Filter by actor |
| `--tool` | Filter by tool (`codex` or `claude`) |
| `--tag` | Filter by tag (repeatable) |

### `ais changelog lint`

Validate existing changelog entries for quality issues.

```bash
ais changelog lint                           # Scan all entries
ais changelog lint --actor myusername        # Filter by actor
ais changelog lint --fix                     # Re-evaluate and fix issues
ais changelog lint --fix --dry-run           # Preview what would be fixed
```

| Option | Description |
|--------|-------------|
| `--project-root` | Git repo root |
| `--actor` | Filter by actor |
| `--fix` | Re-evaluate entries with validation errors |
| `--evaluator` | Evaluator for `--fix`: `codex` (default) or `claude` |
| `--dry-run` | Preview fixes without making changes |

### `ais changelog refresh-metadata`

Recompute entry metadata (`touched_files`, `tests`, `commits`) from stored transcripts without re-running the evaluator.

This is useful if a parser bug was fixed (e.g., file touches weren’t detected correctly) and you want to update historical entries without spending evaluator tokens.

```bash
ais changelog refresh-metadata --project-root "$(git rev-parse --show-toplevel)" --dry-run
ais changelog refresh-metadata --project-root "$(git rev-parse --show-toplevel)" --actor myusername
ais changelog refresh-metadata --project-root "$(git rev-parse --show-toplevel)" --actor myusername --all
```

| Option | Description |
|--------|-------------|
| `--project-root` | Git repo root |
| `--actor` | Filter by actor |
| `--only-empty/--all` | Refresh only entries with empty `touched_files` (default) or all entries |
| `--dry-run` | Preview changes without writing |

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
| [docs/skills.md](docs/skills.md) | Manual skill installation for Codex and Claude |
| [docs/changelog.md](docs/changelog.md) | Changelog generation |
| [docs/source-matching.md](docs/source-matching.md) | How source file matching works |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Common issues and fixes |
| [docs/privacy.md](docs/privacy.md) | Privacy and safety considerations |
| [docs/development.md](docs/development.md) | Contributing and development |
| [docs/pypi.md](docs/pypi.md) | Publishing to PyPI |

---

*Because every debugging session teaches something worth remembering.*
