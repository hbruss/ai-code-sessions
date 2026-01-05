# Source Matching (Concurrent Sessions)

When you use `ais ctx`, it needs to find the correct native log file after your AI session ends. This becomes non-trivial when you have multiple sessions running concurrently—which log file belongs to which session?

This document explains how the source matching algorithm works and how to debug issues.

---

## The Problem

AI coding tools write session logs to specific locations:

- **Codex**: `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`
- **Claude**: `~/.claude/projects/<encoded-path>/<uuid>.jsonl`

When you run `ais ctx "Fix bug" --codex`, we need to:

1. Wait for you to finish working
2. Find the log file that corresponds to *this specific session*
3. Export it to your project directory

With concurrent sessions (multiple terminals, different projects), there might be several log files with overlapping timestamps. The matching algorithm ensures we pick the right one.

---

## How Matching Works

### Step 1: Capture Timestamps

When `ais ctx` starts, it records the current UTC timestamp. When the underlying tool exits, it records another timestamp. These define the "session window."

### Step 2: Find Candidate Files

**For Codex:**

1. Look in `~/.codex/sessions/YYYY/MM/DD/` (or `$CODEX_HOME/sessions/YYYY/MM/DD/` if `CODEX_HOME` is set) for the relevant dates (±1 day from session)
2. Date folders are determined using **local time** (your system timezone), not UTC—this can explain edge cases when sessions span midnight
3. Filter to files named `rollout-*.jsonl`
4. Check file modification time (`mtime`) is within `[start - 15min, end + 15min]`

**For Claude:**

1. Encode the project root as Claude does: the absolute path with slashes replaced by hyphens and a leading hyphen (e.g., `/Users/you/project` becomes `-Users-you-project`)
2. Look in `~/.claude/projects/<encoded-path>/`
3. If that doesn't exist, fall back to `~/.claude/projects/<encoded-cwd>/`
4. If neither exists, scan all project folders (slower)
5. Filter to `*.jsonl` files (excluding `agent-*.jsonl`)
6. Check `mtime` within the session window

### Step 3: Extract Session Metadata

For each candidate file:

1. Read the first JSONL line to get session start timestamp and working directory
2. Read the last JSONL line to get session end timestamp
3. If timestamps aren't in the log, fall back to file `mtime`

### Step 4: Validate Working Directory

The session's working directory (from the log) must match the `--cwd` argument passed to `ais ctx`. This uses `realpath()` normalization to handle symlinks and relative paths.

Files that don't match the working directory are excluded.

### Step 5: Score and Select

For each remaining candidate, calculate a score:

```
score = |session_start - arg_start| + |session_end - arg_end|
```

The file with the **lowest score** (smallest timestamp delta) wins.

---

## What Gets Written: `source_match.json`

Every export writes a `source_match.json` file to help with debugging:

```json
{
  "best": {
    "path": "/Users/you/.codex/sessions/2026/01/02/rollout-abc123.jsonl",
    "score": 0.523,
    "cwd_match": true,
    "start_ts": "2026-01-02T14:35:00.123Z",
    "end_ts": "2026-01-02T16:22:45.678Z"
  },
  "candidates": [
    {
      "path": "/Users/you/.codex/sessions/2026/01/02/rollout-def456.jsonl",
      "score": 1842.1,
      "cwd_match": false,
      "start_ts": "2026-01-02T14:00:00.000Z",
      "end_ts": "2026-01-02T15:30:00.000Z"
    }
    // ... up to 25 candidates
  ]
}
```

**Fields:**

| Field | Description |
|-------|-------------|
| `path` | Full path to the log file |
| `score` | Lower is better (seconds of timestamp delta) |
| `cwd_match` | Whether the working directory matched |
| `start_ts` | First timestamp in the log |
| `end_ts` | Last timestamp in the log |

---

## Debugging Source Matching

### Check What Was Selected

```bash
# View the selected file
cat .codex/sessions/*/source_match.json | jq .best.path

# View full details
cat .codex/sessions/*/source_match.json | jq .best
```

### View All Candidates

```bash
# See top 5 candidates with scores
cat .codex/sessions/*/source_match.json | jq '.candidates[:5] | .[] | {path, score, cwd_match}'
```

### Debug Candidate Selection

Use `find-source` with debug output:

```bash
ais find-source \
  --tool codex \
  --cwd "$PWD" \
  --project-root "$(git rev-parse --show-toplevel)" \
  --start 2026-01-02T14:35:00Z \
  --end 2026-01-02T16:22:00Z \
  --debug-json /tmp/source_debug.json

# Examine the output
cat /tmp/source_debug.json | jq '.candidates | length'
cat /tmp/source_debug.json | jq '.candidates[:10]'
```

---

## Common Issues

### Wrong Session Selected

**Symptom:** Transcript shows content from a different session.

**Cause:** Two sessions had very close timestamps and the scoring picked the wrong one.

**Solution:** Re-export with the correct file:

```bash
# First, find the correct file in candidates
cat .codex/sessions/my-session/source_match.json | jq '.candidates[] | .path'

# Then re-export
ais json /correct/path/to/rollout-xyz.jsonl \
  -o .codex/sessions/my-session \
  --json
```

### No Candidates Found

**Symptom:** "No matching Codex/Claude session files found"

**Causes:**

1. Session didn't write any logs (tool not running, crashed, etc.)
2. Looking in wrong date range
3. Working directory mismatch

**Debug steps:**

```bash
# Check if logs exist
ls -la ~/.codex/sessions/2026/01/02/

# Check file timestamps
ls -la ~/.codex/sessions/2026/01/02/rollout-*.jsonl

# Check working directory in a log
head -1 ~/.codex/sessions/2026/01/02/rollout-abc.jsonl | jq .cwd
```

### CWD Mismatch

**Symptom:** Correct file exists but isn't selected.

**Cause:** The working directory in the log doesn't match where you ran `ais ctx`.

**Debug:**

```bash
# Check what CWD the log has
head -1 ~/.codex/sessions/2026/01/02/rollout-abc.jsonl | jq .cwd

# Compare to where you ran ais ctx
pwd

# Check for symlink differences
realpath .
```

**Solution:** The matching uses `realpath()` normalization. If paths differ after normalization, they won't match. You may need to run `ais ctx` from the same directory the AI tool sees.

---

## Manual Override: Direct Export

If matching fails, bypass it entirely:

```bash
# Export a known file directly
ais json ~/.codex/sessions/2026/01/02/rollout-xyz.jsonl \
  -o .codex/sessions/2026-01-02-1435_My_Session \
  --label "My Session" \
  --json \
  --open
```

This creates the transcript from a specific file without any matching logic.

---

## Tips for Reliable Matching

### Avoid Overlapping Sessions

If you run multiple sessions in the same directory with overlapping time ranges, matching gets harder. Consider:

- Working on different projects in different terminals
- Waiting for one session to finish before starting another

### Use Descriptive Labels

Labels don't affect matching, but they help you identify sessions later:

```bash
ais ctx "Fix auth bug in checkout" --codex
```

### Check Timestamps

If matching seems wrong, compare session window to file mtimes:

```bash
# When did the session run?
cat .codex/sessions/*/source_match.json | jq '.best | {start_ts, end_ts}'

# When were files modified?
ls -la ~/.codex/sessions/2026/01/02/ | grep rollout
```

---

## Technical Details

### Path Normalization

Working directories are compared using `realpath()` which:

- Resolves symlinks
- Expands `~` to home directory
- Removes trailing slashes
- Converts to absolute path

### Timestamp Handling

- Primary source: `created_at` / `timestamp` fields in first/last JSONL lines
- Fallback: File modification time (`mtime`)
- All timestamps normalized to UTC for comparison

### Scoring Function

```python
score = abs(session_start - arg_start).total_seconds() +
        abs(session_end - arg_end).total_seconds()
```

A score of `0.0` means perfect match. Typical scores are under `1.0` for correct matches.
