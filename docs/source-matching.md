# Source matching (concurrent sessions)

`ais ctx` exports transcripts by converting the **native JSONL log file** written by Codex/Claude.

When you have multiple sessions running at the same time, `ais ctx` needs a robust way to select “the right” source file for a given run. That logic lives in:

- `ai-code-sessions find-source`
- `ai-code-sessions export-latest` (calls `find-source` internally)

## Inputs used for matching

`ais ctx` captures and passes:

- `--tool` (`codex` or `claude`)
- `--cwd` (the directory where you started the session)
- `--project-root` (git root; used for Claude project-folder lookup)
- `--start` / `--end` (UTC timestamps taken immediately before and after the CLI process runs)

## How matching works

### Codex

Searches:

- `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`

Constraints (performance + correctness):

- Restricts search to a small set of date folders: `(start_date, end_date) ± 1 day` (based on local time).
- Filters to files whose `mtime` falls in the window `[start-15min, end+15min]`.
- Reads first/last JSONL objects to infer:
  - session `cwd` (must match `--cwd`, using `realpath()` normalization)
  - start/end timestamps (falls back to file `mtime` if missing)

Scoring:

- Picks the file with the smallest `|start-start_arg| + |end-end_arg|` (seconds).

### Claude Code

Searches:

- `~/.claude/projects/<encoded-project-root>/*.jsonl` (preferred)
- falls back to `~/.claude/projects/<encoded-cwd>/*.jsonl`
- if neither exist, scans all project folders (slower)

Filters:

- Excludes `agent-*.jsonl` by default.
- Filters to files whose `mtime` falls in `[start-15min, end+15min]`.
- Reads first/last JSONL objects to infer:
  - session `cwd` (must match `--cwd` or `--project-root`, using `realpath()` normalization)
  - start/end timestamps (falls back to file `mtime` if missing)

Scoring is the same as Codex.

## What gets written: `source_match.json`

`export-latest` always writes a `source_match.json` file into the session output directory. It contains:

- `best`: the chosen file + metadata
- `candidates`: up to the next-best 25 candidates and their scores

This is the first thing to check when a transcript looks “wrong”.

## Debugging and overrides

Install with `pipx install ai-code-sessions` to get `ai-code-sessions` / `ais` on your `PATH`.

### Debug candidate selection

```bash
ai-code-sessions find-source \
  --tool codex \
  --cwd "$PWD" \
  --project-root "$(git rev-parse --show-toplevel)" \
  --start "$START" \
  --end "$END" \
  --debug-json /tmp/source_debug.json
```

### Re-export using a known file

If you already know the correct JSONL file (or you have a copied JSONL in the session directory), bypass matching:

```bash
ai-code-sessions json /path/to/rollout-123.jsonl -o /path/to/session-dir --label "My label" --json
```

This overwrites `index.html` and `page-*.html` in that directory.
