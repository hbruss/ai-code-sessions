# Troubleshooting

This guide covers common issues and how to resolve them.

---

## Quick Diagnostics

Before diving into specific issues, try these quick checks:

```bash
# 1. Verify installation
ais --version
ais --help

# 2. Check if AI tools are installed
codex --version
claude --version

# 3. Verify session logs exist
ls -la ~/.codex/sessions/
ls -la ~/.claude/projects/

# 4. Check recent session directory
ls -la .codex/sessions/ || ls -la .claude/sessions/
```

---

## Export Issues

### `ais ctx` didn't generate `index.html`

**Check for:**

1. **`source_match.json` missing** — The export step didn't run

   ```bash
   ls .codex/sessions/*/source_match.json
   ```

   If missing, the matching step failed. See "No matching files found" below.

2. **`source_match.json` exists but no HTML** — Export failed after matching

   ```bash
   cat .codex/sessions/*/source_match.json | jq .best.path
   ```

   Try exporting manually:

   ```bash
   ais json /path/from/source_match.json \
     -o .codex/sessions/my-session \
     --open
   ```

### "No matching Codex rollout files found"

**Causes:**

1. Codex didn't write any logs
2. Logs are in an unexpected location
3. Time window doesn't match

**Solutions:**

```bash
# Check if any Codex logs exist
find ~/.codex/sessions -name "rollout-*.jsonl" | head -5

# List today's logs
ls -la ~/.codex/sessions/$(date +%Y)/$(date +%m)/$(date +%d)/

# Try exporting a known file directly
ais json ~/.codex/sessions/2026/01/02/rollout-abc.jsonl -o ./test --open
```

### "No matching Claude session files found"

**Causes:**

1. Claude didn't write any logs
2. Project path encoding mismatch
3. Time window doesn't match

**Solutions:**

```bash
# Check if any Claude logs exist
find ~/.claude/projects -name "*.jsonl" | head -5

# List all project folders
ls ~/.claude/projects/

# Try exporting a known file directly
ais json ~/.claude/projects/*/abc123.jsonl -o ./test --open
```

### Transcript shows wrong session content

**Cause:** Source matching selected the wrong file.

**Solution:**

```bash
# Check what was selected
cat .codex/sessions/my-session/source_match.json | jq .best

# Find the correct file in candidates
cat .codex/sessions/my-session/source_match.json | jq '.candidates[] | .path'

# Re-export with correct file
ais json /correct/path/to/rollout.jsonl \
  -o .codex/sessions/my-session \
  --json
```

See [source-matching.md](source-matching.md) for detailed debugging.

---

## CLI Issues

### "Command not found: 'ais'"

**Cause:** CLI not installed or not on PATH.

**Solutions:**

```bash
# Install with pipx
pipx install ai-code-sessions
pipx ensurepath

# Or check if it's already installed elsewhere
which ai-code-sessions

# Restart your shell or source profile
source ~/.bashrc  # or ~/.zshrc
```

### "Command not found: 'codex'" / "Command not found: 'claude'"

**Cause:** AI tool not installed or not on PATH.

**Solutions:**

```bash
# Check if installed
which codex
which claude

# Set custom path via environment
export CTX_CODEX_CMD="/path/to/codex"
export CTX_CLAUDE_CMD="/path/to/claude"
```

### "Error: Missing option '--output'"

**Cause:** Using `ais json` without specifying output directory.

**Solution:**

```bash
# Specify output directory
ais json session.jsonl -o ./output-dir

# Or use auto-naming
ais json session.jsonl -a
```

---

## Changelog Issues

### Changelog generation failed (only `failures.jsonl`)

**Check the failure details:**

```bash
# View recent failures
tail -1 .changelog/*/failures.jsonl | jq .

# See the error message
tail -1 .changelog/*/failures.jsonl | jq '.error'

# See stderr (most actionable)
tail -1 .changelog/*/failures.jsonl | jq '.stderr_tail'
```

### "usage_limit_reached" / "HTTP 429"

**Cause:** Rate limited by the evaluator API.

**Solutions:**

1. Wait for your usage to reset
2. Backfill will halt early on rate limits—rerun later
3. Use a different evaluator:

   ```bash
   ais changelog backfill --evaluator claude  # If using codex
   ais changelog backfill --evaluator codex   # If using claude
   ```

### "Codex ran out of room in the model's context window"

**Cause:** Session is very large.

**What happens:** The evaluator automatically retries with a smaller "budget" digest. If that still fails, the entry is recorded in `failures.jsonl`.

**Solutions:**

1. Accept that very large sessions may not get changelog entries
2. Consider splitting large tasks into multiple smaller sessions

### "codex: command not found" in changelog generation

**Cause:** Codex CLI not installed or not in PATH.

**Solutions:**

```bash
# Install Codex
npm install -g @openai/codex  # or your installation method

# Or use Claude evaluator instead
export CTX_CHANGELOG_EVALUATOR="claude"
```

### Backfill is slow

**Cause:** Processing many sessions sequentially.

**Solution:** Use Claude evaluator with concurrency:

```bash
ais changelog backfill \
  --evaluator claude \
  --max-concurrency 5
```

---

## Configuration Issues

### Config file not being read

**Check config locations:**

```bash
# Show where config files should be
ais setup

# Check if files exist
cat ~/.config/ai-code-sessions/config.toml        # Linux
cat ~/Library/Application\ Support/ai-code-sessions/config.toml  # macOS

# Check per-repo config
cat .ai-code-sessions.toml
cat .ais.toml
```

### Environment variables not working

**Debug:**

```bash
# Check current values
env | grep -E '^(CTX_|AI_CODE_SESSIONS_|CHANGELOG_)' | sort

# Verify they're exported (not just set)
echo $CTX_ACTOR
```

### Wrong timezone in folder names

**Check config:**

```bash
# Current timezone setting
grep tz ~/.config/ai-code-sessions/config.toml
grep tz .ai-code-sessions.toml

# Override via environment
export CTX_TZ="America/New_York"
```

---

## Viewing Transcripts

### How to open `index.html`

```bash
# macOS
open .codex/sessions/*/index.html

# Linux
xdg-open .codex/sessions/*/index.html

# Windows
start .codex/sessions/*/index.html

# With ais json
ais json session.jsonl -o ./out --open
```

### Transcript looks broken / unstyled

**Possible causes:**

1. Browser blocked local file access
2. CSS not embedded (shouldn't happen with current version)

**Solutions:**

1. Try a different browser
2. Check browser console for errors (F12)
3. Regenerate the transcript:

   ```bash
   ais json /path/to/source.jsonl -o ./session-dir --json
   ```

### Legacy PTY sessions (no `index.html`)

Older sessions may have:

- `trace.html` — Raw PTY recording
- `transcript.md` — Markdown transcript

These use a different format. To convert if you have the JSONL:

```bash
ais json ./session/rollout-*.jsonl -o ./session --json
```

---

## Performance Issues

### `ais ctx` slow to start

**Cause:** Loading large config or scanning many sessions.

**Solutions:**

1. Check for corrupt config files
2. Archive old session directories

### Backfill taking forever

**Solutions:**

```bash
# Use Claude with concurrency
ais changelog backfill --evaluator claude --max-concurrency 5

# Limit scope
ais changelog backfill --limit 10

# Process specific directory
ais changelog backfill --sessions-dir ./.codex/sessions/2026-01-02-*
```

### Large session exports slow

**Cause:** Processing thousands of messages.

**Note:** This is expected for very large sessions. The rendering is single-threaded.

---

## Getting Help

### Collect diagnostic info

When reporting issues:

```bash
# Version info
ais --version
python --version
uv version

# Environment
env | grep -E '^(CTX_|AI_CODE_SESSIONS_)' | sort

# Recent failure (if applicable)
tail -1 .changelog/*/failures.jsonl

# Source matching info (if applicable)
cat .codex/sessions/*/source_match.json | jq .best
```

### File an issue

Include:

1. What you were trying to do
2. What happened (error message, unexpected behavior)
3. Diagnostic info from above
4. Steps to reproduce

Report issues at: https://github.com/hbruss/ai-code-sessions/issues
