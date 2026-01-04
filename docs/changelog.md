# Changelog generation

`ai-code-sessions` can optionally generate an **engineering-oriented changelog entry** after exporting a transcript.

The changelog is designed to be:

- **Append-only** (JSONL)
- **Committable (opt-in)** — lives in your project repo, but you may want to ignore it by default
- **Searchable** (easy to `rg` by file path, tag, or phrasing)
- **Low-noise** (paraphrased context, links back to the full transcript)

## Where it writes

In the **project repo** you ran `ais ctx` in:

- `.changelog/<actor>/entries.jsonl` — one JSON object per `ais ctx` exit (per actor)
- `.changelog/<actor>/failures.jsonl` — best-effort failure log (export remains successful)

In the **session output directory** (for resume/backfill support):

- `export_runs.jsonl` — records each export run window (`--start/--end`) for that session dir

Internally, changelog generation runs `codex exec` in non-interactive mode and uses an isolated temporary `CODEX_HOME` so it doesn't need to write to your main `~/.codex` directory. Backfill can optionally use Claude Code CLI (`claude`) as the evaluator instead.

## Enable it

### With `ais ctx`

Set an actor (recommended: your GitHub username) and enable changelog generation:

```bash
export CTX_ACTOR="your-github-username"
export CTX_CHANGELOG=1
```

Tip: if you don’t want these files committed (recommended for public repos), add `.changelog/` to your project’s `.gitignore`.

### With config

Run the setup wizard:

```bash
ais setup
```

Or create a per-repo config (see `docs/config.md`):

```toml
[changelog]
enabled = true
actor = "your-github-username"
```

### With the CLI

```bash
ai-code-sessions export-latest ... --changelog
```

Tip: set `CTX_ACTOR` / `CHANGELOG_ACTOR` (or pass `--changelog-actor`) so entries land in your own `.changelog/<actor>/...` files.

### Evaluator overrides (optional)

By default, changelog evaluation runs with Codex (`gpt-5.2`, `xhigh` reasoning). You can override this per-session:

```bash
export CTX_CHANGELOG_EVALUATOR="claude"   # or "codex"
export CTX_CHANGELOG_MODEL="opus"        # model for the selected evaluator
export CTX_CHANGELOG_CLAUDE_THINKING_TOKENS="8192"  # Claude-only (optional)
```

## Backfill

To generate entries for existing session output directories:

```bash
ai-code-sessions changelog backfill --project-root "$(git rev-parse --show-toplevel)"
```

You can also point at a specific sessions directory:

```bash
ai-code-sessions changelog backfill --sessions-dir ./.codex/sessions
```

Notes:

- If `export_runs.jsonl` is present, backfill can generate **delta-only** entries for resumed sessions.
- Without it, backfill will create a best-effort single entry per session directory.
- To keep entries low-noise and cheap to generate, the evaluator digest **does not include command output** unless a tool call is marked as an error (then a short tail is included for context).
- If the evaluator reports a context window overflow (or Claude times out), the evaluator retries once using a smaller “budget” digest.
- If the evaluator returns a usage/rate limit (`HTTP 429` / `usage_limit_reached`), backfill halts early so you can rerun later without generating a long list of failures.

Backfill evaluator:

- Default: Codex (`gpt-5.2`, `xhigh` reasoning)
- Optional: Claude Code CLI (`opus`, max thinking)

Claude concurrency:

- When using `--evaluator claude`, backfill runs up to 5 evaluations concurrently by default.
- Override with `--max-concurrency N` (and use `--max-concurrency 1` if you need `--limit`).
