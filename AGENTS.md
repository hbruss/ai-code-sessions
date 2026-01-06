---
description: "Convert Codex CLI + Claude Code session logs (JSON/JSONL) into browsable, paginated HTML transcripts; optionally generate per-repo changelog entries via `codex exec` (or Claude evaluator)."
language: python
cli: ai-code-sessions
---

# Repository Guidelines

## Project Structure & Module Organization

- `src/ai_code_sessions/cli.py`: Click CLI commands (`ai-code-sessions` / `ais` entrypoints)
- `src/ai_code_sessions/core.py`: log parsing, source matching, HTML export, changelog generation helpers
- `src/ai_code_sessions/__init__.py`: facade/back-compat + `main()` entrypoint wiring
- `src/ai_code_sessions/templates/`: Jinja2 templates used to render `index.html` + `page-*.html`
- `tests/`: pytest suite + Syrupy snapshots in `tests/__snapshots__/`
- `docs/`: usage + architecture notes (see also Simon Willison’s `claude-code-transcripts` for upstream reference)
- Scratch space: `.tmp/` (work-in-progress artifacts) and `.archive/` (retained artifacts)
- Changelog output (written into the *target project repo*): `.changelog/<actor>/entries.jsonl` and `.changelog/<actor>/failures.jsonl`

## Build, Test, and Development Commands

- `uv run --group dev pytest`: run tests (dev dependencies live in the `dev` group)
- `uv run --group dev pytest --snapshot-update`: update Syrupy snapshots (use only for intentional HTML changes)
- `uv run --project . ai-code-sessions --help`: run the CLI from this repo
- `uv build --out-dir dist --clear`: build wheel + sdist (for PyPI)
- `twine check dist/*`: validate built artifacts before upload
- `uv run --project . ai-code-sessions json /path/to/session.jsonl -o ./out --label "My label"`: convert a specific JSON/JSONL log
- `uv run --project . ai-code-sessions export-latest ... --changelog`: export + append a changelog entry (optional; actor comes from config/env, or override with `--changelog-actor`)
- `uv run --project . ai-code-sessions changelog backfill --project-root "$(git rev-parse --show-toplevel)" --actor "$CTX_ACTOR"`: backfill changelog from existing session dirs

## Coding Style & Naming Conventions

- Python `>=3.11`; 4-space indentation; keep changes consistent with existing formatting.
- Prefer small, well-named functions with docstrings; use `Path` for filesystem paths.
- Naming: `snake_case` for functions/variables, `CAPS_SNAKE_CASE` for constants.
- Keep templates tool-agnostic: parsers should normalize raw logs into the “logline” shape rendered by templates.
- Treat changelog generation as best-effort: export should succeed even if changelog generation fails.

## Testing Guidelines

- Add tests under `tests/` and keep fixtures minimal and explicit.
- Snapshot tests validate generated HTML; update snapshots only when output changes are intentional.

## Python Linting and Formatting

- Use **Ruff** exclusively for linting and formatting (no Black, isort, Flake8, or other tools).
- Run Ruff via `uv` to keep versions consistent across machines/CI: `uv run --group dev ruff ...`
- After any changes to Python files:
  - Lint + auto-fix: `uv run --group dev ruff check --fix <paths>`
  - Format: `uv run --group dev ruff format <paths>`
- Before commits/PRs that touch Python, prefer non-mutating checks:
  - `uv run --group dev ruff check <paths>`
  - `uv run --group dev ruff format --check <paths>`
- Respect Ruff config in `pyproject.toml` (e.g., line length, ignores, rule selection).
- Ensure Ruff is available in the dev group (don’t rely on a global install): `uv add --group dev ruff`

### Common Commands

| Task | Command |
|------|---------|
| Lint (check only) | `uv run --group dev ruff check .` |
| Lint + auto-fix | `uv run --group dev ruff check --fix .` |
| Format | `uv run --group dev ruff format .` |
| Format (check only) | `uv run --group dev ruff format --check .` |
| Full lint + format | `uv run --group dev ruff check --fix . && uv run --group dev ruff format .` |

## Commit & Pull Request Guidelines

- Use Conventional Commits (seen in history): `fix(ci): ...`, `chore: ...`; prefer `feat(scope): ...` / `fix(scope): ...`.
- PRs should include: purpose, linked issues, local test results (`uv run --group dev pytest`), and screenshots/GIFs when HTML rendering changes.

## Security & Configuration Tips

- Treat transcripts/exports as sensitive; review/redact before sharing or using `--gist` uploads.
- Don’t delete/move/overwrite repo files without explicit permission; use `.tmp/` for scratch work and move obsolete artifacts into `.archive/` for retention.
- Use `markitdown` for ad-hoc doc/PDF conversions and store intermediate outputs in `.tmp/`.
- Config: `AI_CODE_SESSIONS_CONFIG` overrides the default config location (macOS: `~/Library/Application Support/ai-code-sessions/config.toml`; Linux: `$XDG_CONFIG_HOME/ai-code-sessions/config.toml` or `~/.config/...`; Windows: `%APPDATA%\\ai-code-sessions\\config.toml`).
