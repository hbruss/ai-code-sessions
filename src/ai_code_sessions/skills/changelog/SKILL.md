---
name: changelog
description: Use this skill when working in a repository that uses `ai-code-sessions` for session tracking. The changelog provides a structured history of all AI coding sessions, bridging context gaps between sessions and serving as project memory.
---

# ai-code-sessions Changelog Skill

## Tool Selection

Use the right tool for each task:

| Task | Tool | Why |
|------|------|-----|
| Recent entries by date | `ais changelog since` | Best for date/commit filtering |
| Validate entry quality | `ais changelog lint` | Detects truncation, garbage |
| Text search (topics, keywords) | `rg` | Fast, focused output |
| Check if topic was discussed | `rg -l` | Returns only filenames |
| Sort by date | `jq` | Structured field access |
| Extract specific fields | `jq` | JSON transformation |
| Filter by tag/date range | `jq` | Structured queries |
| Get last N entries | `tail` + `jq` | Simple and fast |

## Quick Start

```bash
# Check if changelog exists
ls .changelog/*/entries.jsonl 2>/dev/null

# Get last 3 entries (summary view)
tail -3 .changelog/*/entries.jsonl | jq '{summary, tags, bullets}'

# Search for any mention of a topic
rg -i "authentication" .changelog/

# Find which sessions touched a file
rg "auth.py" .changelog/*/entries.jsonl
```

## Changelog Location

The changelog lives at the repository root:

```
.changelog/
└── <actor>/
    ├── entries.jsonl    # Successful session entries (append-only)
    └── failures.jsonl   # Failed generation attempts (for debugging)
```

The `<actor>` is a slugified identifier (usually a GitHub username or email). A repo may have multiple actor directories if different people have contributed sessions.

Changelog timestamps are ISO8601 with explicit offsets (usually UTC); folder names use ctx.tz (America/Los_Angeles).

## Entry Schema (v1)

Each line in `entries.jsonl` is a JSON object:

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | int | Always `1` for current format |
| `run_id` | string | Content-addressed hash (16 chars), unique per session |
| `created_at` | string | ISO 8601 timestamp when entry was generated |
| `tool` | string | `"codex"`, `"claude"`, or `"unknown"` |
| `actor` | string | Who ran the session |
| `project` | string | Project/repo name |
| `label` | string\|null | Human-friendly session label |
| `start` | string | Session start timestamp (ISO 8601) |
| `end` | string | Session end timestamp (ISO 8601) |
| `continuation_of_run_id` | string\|null | Links resumed sessions to their parent |
| `transcript` | object | Paths to HTML transcript and source files |
| `summary` | string | One-line description (max 500 chars) |
| `bullets` | array[string] | 1-12 specific accomplishments |
| `tags` | array[string] | 0-24 classification tags (max 64 chars each) |
| `touched_files` | object | Files created/modified/deleted/moved |
| `tests` | array[object] | Test commands and results |
| `commits` | array[object] | Git commits made during session |

### Nested Objects

**transcript:**
```json
{
  "index_html": "/path/to/sessions/2026-01-02_My_Session/index.html",
  "source_jsonl": "/path/to/sessions/2026-01-02_My_Session/rollout-*.jsonl"
}
```

**touched_files:**
```json
{
  "created": ["src/new_file.py"],
  "modified": ["src/existing.py"],
  "deleted": ["old_file.py"],
  "moved": [{"from": "old/path.py", "to": "new/path.py"}]
}
```

**tests:** `[{"cmd": "pytest tests/", "result": "pass"}]` (result: pass/fail/unknown)

**commits:** `[{"hash": "7aae833", "message": "fix: resolve auth bug"}]`

## Search Patterns (ripgrep)

### Find Sessions Mentioning a Topic

```bash
# Case-insensitive search in changelog entries
rg -i "authentication" .changelog/*/entries.jsonl

# Show just matching files (quick check)
rg -l "OAuth" .changelog/

# Search with context lines
rg -C 2 "database migration" .changelog/*/entries.jsonl
```

### Find Sessions That Touched a File

```bash
# Direct filename match
rg "src/auth/login.py" .changelog/*/entries.jsonl

# Partial path match
rg "login\.py" .changelog/*/entries.jsonl

# Find any work in a directory
rg '"(created|modified)".*"src/auth/' .changelog/*/entries.jsonl
```

### Find Sessions by Tag

```bash
# Exact tag match
rg '"tags":\s*\[[^\]]*"bugfix"' .changelog/*/entries.jsonl

# Simpler: just search for tag in context
rg "bugfix" .changelog/*/entries.jsonl
```

### Find Failing Tests

```bash
rg '"result":\s*"fail"' .changelog/*/entries.jsonl
```

### Find WIP or In-Progress Work

```bash
rg -i "wip|part [0-9]|incomplete|todo" .changelog/*/entries.jsonl
```

## Structured Queries (jq)

Use jq when you need sorting, filtering by date, or field extraction.

### Get Recent Entries (Sorted)

```bash
# Last 5 entries, sorted by timestamp
cat .changelog/*/entries.jsonl | jq -s 'sort_by(.created_at) | .[-5:]'

# Compact view of recent work
cat .changelog/*/entries.jsonl | jq -s '
  sort_by(.created_at) | .[-5:] | .[] | 
  {summary, tags, bullets: .bullets[:3]}
'
```

### Filter by Date Range

```bash
jq -s --arg start "2026-01-01" --arg end "2026-01-31" '
  [.[] | select(.start >= $start and .start <= $end)]
' .changelog/*/entries.jsonl
```

### Extract All Commits

```bash
cat .changelog/*/entries.jsonl | jq -s '[.[].commits[]] | unique_by(.hash)'
```

### Get All Unique Tags Used

```bash
cat .changelog/*/entries.jsonl | jq -s '[.[].tags[]] | unique | sort'
```

### Find Continuation Chains

```bash
# Find sessions that continued from a specific run
jq -s --arg id "abc123" '
  [.[] | select(.run_id == $id or .continuation_of_run_id == $id)]
' .changelog/*/entries.jsonl
```

### List Recently Modified Files

```bash
cat .changelog/*/entries.jsonl | jq -s '
  sort_by(.created_at) | .[-10:] | 
  [.[].touched_files | .created[], .modified[]] | 
  unique
'
```

## CLI Commands

The `ai-code-sessions` CLI provides purpose-built commands for common tasks.

### Query by Date/Commit

```bash
# Since a specific date
ais changelog since 2026-01-06

# Relative dates
ais changelog since yesterday
ais changelog since "3 days ago"
ais changelog since "last week"

# Since a git commit
ais changelog since HEAD~5
ais changelog since abc1234
ais changelog since main

# Output formats
ais changelog since yesterday --format json      # Full JSON
ais changelog since yesterday --format table     # Markdown table
ais changelog since yesterday --format bullets   # Markdown with bullets

# Filtering
ais changelog since yesterday --tool codex       # Only Codex sessions
ais changelog since yesterday --tag feat         # Only feature work
```

### Validate Entries

Scan for quality issues like truncated content or Unicode garbage:

```bash
# Scan all entries
ais changelog lint

# Scan specific actor
ais changelog lint --actor myusername

# Show all entries (including valid ones)
ais changelog lint --verbose
```

### Fix Garbled Entries

Re-evaluate entries with quality issues:

```bash
# Preview what would be fixed
ais changelog lint --fix --dry-run

# Fix using default evaluator (codex)
ais changelog lint --fix

# Fix using Claude
ais changelog lint --fix --evaluator claude
```

**Notes:**
- Creates backup (`entries.jsonl.bak`) before modifying
- Only entries with source transcripts still available can be fixed

### When to Use CLI vs rg/jq

| Use Case | Best Tool |
|----------|-----------|
| Filter by time | `ais changelog since` |
| Search for keywords | `rg "topic" .changelog/` |
| Complex field extraction | `jq` |
| Validate entry quality | `ais changelog lint` |

## Session Priming Strategy

At session start, quickly gather context:

### 1. Quick Context (Minimal)

```bash
# One-liner for recent work
tail -3 .changelog/*/entries.jsonl | jq '{summary, tags}'
```

### 2. Standard Context

```bash
cat .changelog/*/entries.jsonl | jq -s '
  sort_by(.created_at) | .[-5:] | .[] |
  {label, summary, bullets, tags, commits: [.commits[].message]}
'
```

### 3. Check for In-Progress Work

```bash
rg -i "wip|part [0-9]|incomplete" .changelog/*/entries.jsonl
```

### 4. Check for Failing Tests

```bash
rg '"result":\s*"fail"' .changelog/*/entries.jsonl
```

## Transcripts

Each entry's `transcript` field contains paths to the full HTML transcript. Transcripts live in `.codex/sessions/` or `.claude/sessions/` depending on which tool ran the session.

### Get Transcript Path

```bash
# Most recent transcript
tail -1 .changelog/*/entries.jsonl | jq -r '.transcript.index_html'

# Open in browser (macOS)
open "$(tail -1 .changelog/*/entries.jsonl | jq -r '.transcript.index_html')"
```

### Find Transcript for a Specific Session

```bash
# Search for session, then extract transcript path
rg "OAuth" .changelog/*/entries.jsonl | head -1 | jq -r '.transcript.index_html'
```

## Best Practices

### At Session Start

1. Check if `.changelog/` exists
2. `tail -3 .changelog/*/entries.jsonl | jq '{summary, tags}'` for quick context
3. If user references prior work, use `rg` to find relevant sessions
4. Check for WIP or failing tests if continuing work

### For Search Tasks

1. Start with `rg -l` to find which files contain matches
2. Use `rg -C 2` for context around matches
3. Fall back to `jq` only for structured filtering (dates, specific fields)

### For Historical Questions

1. Text search first: `rg "topic" .changelog/`
2. If you need sorted/filtered results, pipe to `jq`
3. Use transcript paths from entries to access full conversation history

## Troubleshooting

### No Changelog Found

```bash
# Check if it exists but is empty
find .changelog -name "entries.jsonl" -exec wc -l {} \;

# Check gitignore
rg "changelog" .gitignore
```

### Multiple Actors

Combine with cat or search across all:
```bash
# ripgrep searches all by default
rg "topic" .changelog/

# jq needs explicit concat
cat .changelog/*/entries.jsonl | jq -s 'sort_by(.created_at)'
```

## Quick Reference

```bash
# Recent work summary
tail -3 .changelog/*/entries.jsonl | jq '{summary, tags}'

# Search for topic
rg -i "topic" .changelog/

# Find file history
rg "filename" .changelog/*/entries.jsonl

# Failing tests
rg '"result":\s*"fail"' .changelog/*/entries.jsonl

# Open latest transcript
open "$(tail -1 .changelog/*/entries.jsonl | jq -r '.transcript.index_html')"
```
