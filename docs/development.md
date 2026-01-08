# Development

This guide covers how to set up a development environment, run tests, and contribute to `ai-code-sessions`.

---

## Requirements

- **Python** `>=3.11`
- **uv** — Fast Python package manager ([installation](https://docs.astral.sh/uv/getting-started/installation/))

---

## Repository Layout

```
ai-code-sessions/
├── src/ai_code_sessions/
│   ├── __init__.py       # Public package facade
│   ├── core.py           # Parsers, exporters, changelog, helpers
│   ├── cli.py            # Click CLI entrypoints
│   └── templates/        # Jinja2 templates for HTML rendering
├── tests/
│   ├── test_*.py         # pytest test files
│   ├── conftest.py       # pytest fixtures
│   └── __snapshots__/    # Syrupy snapshot files
├── docs/                  # Documentation (you are here)
├── .tmp/                  # Scratch space for work-in-progress
├── .archive/              # Retained artifacts
└── pyproject.toml         # Project configuration
```

---

## Setting Up

### Clone and Install

```bash
# Clone the repository
git clone https://github.com/hbruss/ai-code-sessions.git
cd ai-code-sessions

# Install dependencies (including dev tools)
uv sync --group dev
```

### Running the CLI Locally

```bash
# Run any CLI command
uv run --project . ai-code-sessions --help
uv run --project . ais json --help

# Short form
uv run --project . ais ctx "Test session" --codex

# Resume picker (alias: ais ctx-resume)
uv run --project . ais resume codex
```

---

## Running Tests

### Full Test Suite

```bash
uv run --group dev pytest
```

### Specific Tests

```bash
# Run a single test file
uv run --group dev pytest tests/test_generate_html.py

# Run tests matching a pattern
uv run --group dev pytest -k "test_jsonl"

# Verbose output
uv run --group dev pytest -v
```

### Snapshot Tests

We use [Syrupy](https://github.com/tophat/syrupy) for snapshot testing. Snapshots capture expected HTML output.

```bash
# Run tests (will fail if snapshots don't match)
uv run --group dev pytest

# Update snapshots after intentional changes
uv run --group dev pytest --snapshot-update
```

**Important:** Only update snapshots when you've intentionally changed the HTML output. Review the diff carefully.

---

## Code Style

### Formatting

We follow standard Python conventions:

- 4-space indentation
- `snake_case` for functions and variables
- `CAPS_SNAKE_CASE` for constants

### Linting and Formatting with Ruff

We use **Ruff** exclusively for linting and formatting (no Black, isort, Flake8, or other tools). Run Ruff via `uv` to keep versions consistent:

```bash
# Lint (check only)
uv run --group dev ruff check .

# Lint + auto-fix
uv run --group dev ruff check --fix .

# Format
uv run --group dev ruff format .

# Format (check only)
uv run --group dev ruff format --check .

# Full lint + format
uv run --group dev ruff check --fix . && uv run --group dev ruff format .
```

Before commits/PRs that touch Python, prefer non-mutating checks:

```bash
uv run --group dev ruff check .
uv run --group dev ruff format --check .
```

Ruff configuration lives in `pyproject.toml` (line length, ignores, rule selection).

### Guidelines

1. **Small, well-named functions** — Keep functions focused and add docstrings
2. **Use `Path`** — Always use `pathlib.Path` for filesystem operations
3. **Keep templates tool-agnostic** — Parsers normalize logs; templates render normalized data
4. **Graceful degradation** — Export should succeed even if changelog fails

---

## Architecture Overview

### Modular Layout

The CLI lives in `src/ai_code_sessions/cli.py`, and the implementation lives in
`src/ai_code_sessions/core.py`. `__init__.py` re-exports the public surface.

### Key Components

| Component | Purpose |
|-----------|---------|
| CLI commands | Click-based CLI with subcommands |
| Parsers | Convert Codex/Claude logs to normalized "loglines" |
| Source matching | Find the right log file for concurrent sessions |
| HTML generation | Jinja2 templates for paginated output |
| Changelog | Generate structured session summaries |

### Data Flow

```
Native JSONL → Parser → Loglines → Templates → HTML
                                        ↓
                                   Changelog
```

1. **Native JSONL**: Raw logs from Codex or Claude
2. **Parser**: Format-specific parser normalizes to common structure
3. **Loglines**: Unified representation of messages/tool calls
4. **Templates**: Jinja2 templates render to HTML
5. **Changelog**: Optional AI-generated summary

---

## Adding Support for Another Tool

If you want to add support for a new AI coding tool:

### 1. Auto-Detection

Add a function to detect the new format:

```python
def _detect_new_tool_format(data: list | dict) -> bool:
    """Return True if data looks like a NewTool log."""
    # Check for characteristic fields/structure
    pass
```

### 2. Parser

Implement a parser that produces the same "logline" structure:

```python
def _parse_new_tool_log(data: list | dict) -> list[dict]:
    """Convert NewTool log to normalized loglines."""
    loglines = []
    for entry in data:
        # Map to: user/assistant messages, tool calls, tool results, thinking
        loglines.append({
            "type": "user" | "assistant" | "tool_use" | "tool_result" | "thinking",
            # ... other fields
        })
    return loglines
```

### 3. Source Matching (Optional)

If you want `ais ctx` to work:

```python
def _find_new_tool_source(
    cwd: Path,
    project_root: Path,
    start: datetime,
    end: datetime,
) -> Path | None:
    """Find the NewTool log file matching the session window."""
    pass
```

### 4. Tests

Add fixtures and tests:

```python
# tests/test_new_tool.py
def test_parse_new_tool_log(new_tool_fixture):
    result = parse_session_file(new_tool_fixture)
    assert len(result) > 0
    # ... assertions
```

Add snapshot tests if generating HTML.

---

## Changelog Development

### How It Works

1. **Digest creation**: Session content is condensed (prompts + truncated responses + tool calls)
2. **Evaluator invocation**: Digest sent to Codex/Claude for summarization
3. **Retry logic**: If context overflows, retry with smaller digest
4. **Deduplication**: Content-based IDs prevent duplicate entries

### Testing Changelog Locally

Changelog uses real AI APIs, so it's not tested in CI. To test locally:

```bash
# Enable changelog for a test session
export CTX_CHANGELOG=1
export CTX_ACTOR="test"

# Run a session
uv run --project . ais ctx "Test changelog" --codex

# Check results
cat .changelog/test/entries.jsonl | jq .
cat .changelog/test/failures.jsonl | jq .
```

### Notes

- Changelog generation is **best-effort** — failures are logged, not fatal
- The evaluator runs in a subprocess with isolated config
- Very large sessions may exceed context limits

---

## Common Development Tasks

### Adding a New CLI Command

```python
@cli.command("my-command")
@click.option("--my-option", help="Description")
def my_command_cmd(my_option):
    """Short description for --help."""
    # Implementation
    pass
```

### Modifying HTML Output

1. Edit templates in `src/ai_code_sessions/templates/`
2. Run tests: `uv run --group dev pytest`
3. If tests fail, review diffs and update snapshots if intentional:
   ```bash
   uv run --group dev pytest --snapshot-update
   ```

### Adding Configuration Options

1. Add to config parsing in `_load_config()` and `_config_get()`
2. Add environment variable support in the relevant command
3. Update `ais setup` wizard if user-facing
4. Document in `docs/config.md`

---

## Pull Request Guidelines

### Before Submitting

1. **Run tests**: `uv run --group dev pytest`
2. **Check formatting**: Keep consistent with existing code
3. **Update snapshots only if intentional**: Review diffs carefully
4. **Update documentation**: If adding/changing user-facing features

### PR Description Should Include

- **Purpose**: What problem does this solve?
- **Changes**: Brief description of what changed
- **Testing**: How did you verify it works?
- **Screenshots/GIFs**: For HTML rendering changes

### Commit Messages

We use Conventional Commits:

```
feat(cli): add new export option
fix(changelog): handle empty sessions
docs: update troubleshooting guide
chore: update dependencies
refactor(parser): simplify codex parsing
```

---

## Upstream Reference

This project is based on Simon Willison's `claude-code-transcripts` (Apache-2.0).

- Original: [github.com/simonw/claude-code-transcripts](https://github.com/simonw/claude-code-transcripts)
- Blog post: [simonwillison.net/2025/Dec/25/claude-code-transcripts/](https://simonwillison.net/2025/Dec/25/claude-code-transcripts/)

The rendering approach and HTML template design originate from that project.
