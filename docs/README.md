# ai-code-sessions Documentation

Welcome! This documentation covers everything you need to know about `ai-code-sessions`—a tool for transforming AI coding session logs into browsable HTML transcripts.

---

## What This Tool Does

When you work with AI coding assistants like Codex or Claude, every session generates detailed logs. These logs contain everything: your prompts, AI responses, tool calls, file edits, command outputs, and more. But the raw logs are practically unreadable—thousands of lines of JSON.

`ai-code-sessions` transforms these logs into clean, paginated HTML transcripts that you can browse, search, and share. It also optionally generates changelog entries summarizing what each session accomplished.

---

## Quick Start

```bash
# Install
pipx install ai-code-sessions
pipx ensurepath

# Run the setup wizard
ais setup

# Start a session with automatic export
ais ctx "Fix the login bug" --codex

# When you exit, a transcript appears in .codex/sessions/
```

---

## Documentation Index

### Getting Started

| Document | Description |
|----------|-------------|
| [cli.md](cli.md) | Complete CLI reference with all commands and options |
| [ctx.md](ctx.md) | The `ais ctx` workflow for automatic session export |
| [config.md](config.md) | Configuration files, environment variables, and setup |

### Features

| Document | Description |
|----------|-------------|
| [changelog.md](changelog.md) | Automatic changelog generation from sessions |
| [source-matching.md](source-matching.md) | How we find the right log file for concurrent sessions |

### Reference

| Document | Description |
|----------|-------------|
| [troubleshooting.md](troubleshooting.md) | Common issues and solutions |
| [privacy.md](privacy.md) | Privacy considerations and data safety |
| [development.md](development.md) | Contributing, testing, and architecture |
| [pypi.md](pypi.md) | Publishing new releases to PyPI |

---

## Core Workflow

The typical workflow looks like this:

```bash
# 1. Start a session with a descriptive label
ais ctx "Add OAuth support" --codex

# 2. Work normally with Codex/Claude (full colors, interactivity)
# ... do your work ...

# 3. Exit (Ctrl+D or /exit)

# 4. Find your transcript
open .codex/sessions/*/index.html
```

Each session creates:

```
.codex/sessions/2026-01-02-1435_Add_OAuth_support/
├── index.html           # Session timeline with prompt summaries
├── page-001.html        # First 5 conversations (paginated)
├── page-002.html        # More conversations...
├── rollout-abc123.jsonl # Original log file (archived)
├── source_match.json    # How the log was identified
└── export_runs.jsonl    # Export metadata
```

---

## Key Features

### Paginated HTML Transcripts

Long sessions are split into pages (5 conversations each) so they load quickly. The index page shows a timeline of all prompts with:

- Tool call counts (Bash, Edit, Write, etc.)
- Git commits (linked to GitHub)
- Test pass/fail indicators

### Automatic Source Matching

When you run multiple AI sessions concurrently, `ais ctx` intelligently identifies which log file belongs to which session based on timestamps and working directory.

### Changelog Generation

Optionally generate structured summaries:

```json
{
  "summary": "Added OAuth 2.0 support for Google and GitHub",
  "bullets": [
    "Implemented OAuth flow abstraction",
    "Added Google OAuth provider",
    "Added GitHub OAuth provider"
  ],
  "tags": ["feat", "auth"],
  "files_created": ["src/auth/oauth/google.ts", "src/auth/oauth/github.ts"],
  "files_modified": ["src/auth/index.ts"],
  "test_passed": true,
  "commits": ["abc1234", "def5678"]
}
```

### Resume Support

Continue working on the same transcript across multiple sessions:

```bash
# Day 1
ais ctx "Big refactor" --codex

# Day 2
ais ctx "Continue refactor" --codex resume
```

---

## Supported Tools

| Tool | Log Location | Session Type |
|------|--------------|--------------|
| Codex CLI | `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` | `--codex` |
| Claude Code | `~/.claude/projects/<encoded-path>/*.jsonl` | `--claude` |
| Claude Web Export | Downloaded JSON from claude.ai | `ais json` only |

---

## Configuration

Three levels of configuration (highest priority first):

1. **CLI flags**: `--changelog`, `--changelog-actor`, etc.
2. **Environment variables**: `CTX_CHANGELOG`, `CTX_ACTOR`, etc.
3. **Config files**: `.ai-code-sessions.toml` (per-repo) or global config

Run `ais setup` for an interactive wizard that handles all configuration.

---

## Upstream Credit

This project is a fork of [Simon Willison's](https://simonwillison.net/) `claude-code-transcripts` (Apache-2.0). The rendering engine, HTML templates, and presentation logic come from his excellent work.

- Original project: [github.com/simonw/claude-code-transcripts](https://github.com/simonw/claude-code-transcripts)
- Blog post: [simonwillison.net/2025/Dec/25/claude-code-transcripts/](https://simonwillison.net/2025/Dec/25/claude-code-transcripts/)

What this fork adds:

- Codex CLI support
- Automatic source matching for concurrent sessions
- The `ais ctx` workflow
- Changelog generation
- Interactive setup wizard

---

## Getting Help

- **Troubleshooting**: See [troubleshooting.md](troubleshooting.md)
- **Issues**: [github.com/hbruss/ai-code-sessions/issues](https://github.com/hbruss/ai-code-sessions/issues)
