# Development

## Requirements

- Python `>=3.10`
- `uv`

## Repo layout

- `src/ai_code_sessions/__init__.py` — CLI, parsers, exporters
- `src/ai_code_sessions/templates/` — Jinja templates
- `tests/` — pytest + snapshot tests
- `docs/claude-code-transcripts/` — upstream reference (do not treat as the implementation)

## Run tests

```bash
uv run --group dev pytest
```

Note: the test dependencies live in the `dev` dependency group, so `--group dev` is required.

## Run the CLI locally

```bash
uv run --project . ai-code-sessions --help
uv run --project . ai-code-sessions json --help
```

## Adding support for another tool

High-level checklist:

1. Add an auto-detection function for the new JSON/JSONL format.
2. Implement a parser that converts that format into the same internal “logline” structure used by the templates:
   - user/assistant messages
   - thinking blocks (optional)
   - tool calls + tool results (important)
3. Add source-file matching logic (like `find-source`/`export-latest`) if you want `ctx`-style “export on exit” without needing a file path.
4. Add fixtures + snapshot tests.

The goal is to keep templates tool-agnostic: the parser should do the work of translating raw logs into a normalized stream the templates can render.
