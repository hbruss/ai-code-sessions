# ai-code-sessions

**Transform ephemeral AI coding sessions into permanent, browsable artifacts.**

---

## Standing on Simon's Shoulders

This project is a fork of [Simon Willison's](https://simonwillison.net/) `claude-code-transcripts` (Apache-2.0). The core of what makes this tool useful—the parsing logic, the paginated HTML rendering, the thoughtful presentation of tool calls and their outputs, the collapsible sections, the clean typography—**that's all Simon's work**.

I discovered his project through his [blog post](https://simonwillison.net/2025/Dec/25/claude-code-transcripts/) and immediately recognized it as the solution to something I'd been wanting: a way to preserve AI coding sessions as readable artifacts. His code does the hard work of transforming messy JSONL logs into something you'd actually want to read.

What I've added on top:

- **Codex CLI support** (in addition to Claude Code)
- **Automatic source matching** for finding the right log file when running concurrent sessions
- **An `ais ctx` workflow** for naming sessions and organizing exports by project
- **An append-only changelog system** for generating structured summaries

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
.codex/sessions/2026-01-02T14-35_fix-auth-race-condition/
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

## The Changelog System

Beyond transcripts, `ai-code-sessions` can generate structured changelog entries after each session. These aren't commit messages—they're higher-level summaries of what an AI-assisted coding session actually accomplished.

Each entry captures:

- **Summary**: One-line description of the session's purpose
- **Bullets**: 3-5 specific changes or accomplishments
- **Tags**: Classification (`feat`, `fix`, `refactor`, `docs`, etc.)
- **File operations**: What was created, modified, deleted
- **Test results**: Whether the test suite passed
- **Git commits**: Commits made during the session

Entries are stored as append-only JSONL:

```
.changelog/your-username/entries.jsonl
.changelog/your-username/failures.jsonl
```

This creates a machine-readable history of AI-assisted work—perfect for feeding back to future AI sessions as context, or for generating release notes.

## Usage

### Install (recommended)

Install the CLI with `pipx`:

```bash
pipx install ai-code-sessions
pipx ensurepath
```

Optional: run the interactive setup wizard (writes global/per-repo config and can update `.gitignore`):

```bash
ais setup
```

### `ais ctx` (recommended workflow)

Start sessions with natural-language labels:

```bash
ais ctx "Fix the checkout race condition" --codex
ais ctx "Investigate flaky CI tests" --claude
```

When you quit, `ais ctx` automatically:

1. Finds the correct session log (even with concurrent sessions)
2. Generates paginated HTML transcripts
3. Optionally appends a changelog entry
4. Saves everything to your project repo

### Direct CLI Usage

All commands are available via `ais` (short) or `ai-code-sessions` (full).

Convert a specific file:

```bash
ai-code-sessions json /path/to/session.jsonl \
  -o ./out \
  --label "My session" \
  --json \
  --open
```

Export by time window:

```bash
ai-code-sessions export-latest \
  --tool codex \
  --cwd "$PWD" \
  --project-root "$(git rev-parse --show-toplevel)" \
  --start 2026-01-02T07:25:55.212Z \
  --end 2026-01-02T09:16:57.576Z \
  -o ./out \
  --label "My session" \
  --changelog
```

## How Source Matching Works

When you run concurrent AI sessions, identifying which log file belongs to which session becomes non-trivial. This project solves it with intelligent matching:

1. **Date search**: Scans session directories for the date range ±1 day
2. **Modification time filter**: Considers files modified within the session window
3. **Timestamp extraction**: Reads first/last entries to get actual session bounds
4. **Working directory matching**: Verifies the session was started in the expected directory
5. **Scoring**: Minimizes timestamp delta to find the best match

The result is saved to `source_match.json` with the selected file and up to 25 candidates—so you can verify or manually override if needed.

## Architecture

The implementation lives in a single focused module (`src/ai_code_sessions/__init__.py`, ~4,200 lines) with Jinja2 templates for HTML rendering. Key patterns:

- **Format normalization**: Both Codex and Claude logs are parsed into a common "loglines" format
- **Content block handling**: Modern multi-block messages (text + images + tool calls) are rendered correctly
- **Graceful degradation**: Export succeeds even if changelog generation fails
- **Content-addressed deduplication**: Changelog entries have content-based IDs to prevent duplicates

For detailed architecture documentation, see `docs/README.md`.

---

*Because every debugging session teaches something worth remembering.*
