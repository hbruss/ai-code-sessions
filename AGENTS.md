---
description: "Convert Codex CLI + Claude Code session logs (JSON/JSONL) into browsable, paginated HTML transcripts; optionally generate per-repo changelog entries via `codex exec`."
language: python
cli: ai-code-sessions
---

# Repository Guidelines

## Project Structure & Module Organization

- `src/ai_code_sessions/__init__.py`: CLI entrypoint, log parsers, exporters
- `src/ai_code_sessions/templates/`: Jinja2 templates used to render `index.html` + `page-*.html`
- `tests/`: pytest suite + Syrupy snapshots in `tests/__snapshots__/`
- `docs/`: usage + architecture notes; `docs/claude-code-transcripts/` is a vendored upstream reference (not the implementation)
- Scratch space: `.tmp/` (work-in-progress artifacts) and `.archive/` (retained artifacts)
- Changelog output (written into the *target project repo*): `.changelog/<actor>/entries.jsonl` and `.changelog/<actor>/failures.jsonl`

## Build, Test, and Development Commands

- `uv run --group dev pytest`: run tests (dev dependencies live in the `dev` group)
- `uv run --group dev pytest --snapshot-update`: update Syrupy snapshots (use only for intentional HTML changes)
- `uv run --project . ai-code-sessions --help`: run the CLI from this repo
- `uv run --project . ai-code-sessions json /path/to/session.jsonl -o ./out --label "My label"`: convert a specific JSON/JSONL log
- `uv run --project . ai-code-sessions export-latest ... --changelog --changelog-actor "$CTX_ACTOR"`: export + append a changelog entry (optional)
- `uv run --project . ai-code-sessions changelog backfill --project-root "$(git rev-parse --show-toplevel)" --actor "$CTX_ACTOR"`: backfill changelog from existing session dirs

## Coding Style & Naming Conventions

- Python `>=3.10`; 4-space indentation; keep changes consistent with existing formatting.
- Prefer small, well-named functions with docstrings; use `Path` for filesystem paths.
- Naming: `snake_case` for functions/variables, `CAPS_SNAKE_CASE` for constants.
- Keep templates tool-agnostic: parsers should normalize raw logs into the “logline” shape rendered by templates.
- Treat changelog generation as best-effort: export should succeed even if changelog generation fails.

## Testing Guidelines

- Add tests under `tests/` and keep fixtures minimal and explicit.
- Snapshot tests validate generated HTML; update snapshots only when output changes are intentional.

## Commit & Pull Request Guidelines

- Use Conventional Commits (seen in history): `fix(ci): ...`, `chore: ...`; prefer `feat(scope): ...` / `fix(scope): ...`.
- PRs should include: purpose, linked issues, local test results (`uv run --group dev pytest`), and screenshots/GIFs when HTML rendering changes.

## Security & Configuration Tips

- Treat transcripts/exports as sensitive; review/redact before sharing or using `--gist` uploads.
- Don’t delete/move/overwrite repo files without explicit permission; use `.tmp/` for scratch work and move obsolete artifacts into `.archive/` for retention.
- Use `markitdown` for ad-hoc doc/PDF conversions and store intermediate outputs in `.tmp/`.
