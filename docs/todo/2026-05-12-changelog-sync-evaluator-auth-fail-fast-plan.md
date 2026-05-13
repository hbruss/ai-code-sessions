# Changelog Sync Evaluator Auth Fail-Fast Plan

## Goal

Make `ais changelog sync` surface actionable evaluator authentication failures during the run and stop after the first auth failure instead of recording repeated failed changelog rows.

## Scope

- Detect Claude evaluator authentication failures from CLI output such as `api_error_status: 401`, `Invalid authentication credentials`, `Failed to authenticate`, and `Invalid API key`.
- Replace raw Claude JSON tail output with concise recovery guidance when the evaluator auth path is broken.
- Teach `ais changelog sync` to halt after the first evaluator auth failure and print the commands needed to recover or switch evaluators.
- Keep ordinary evaluator failures unchanged.
- Update troubleshooting docs so users can self-serve the fix.

## Tasks

- [x] Add failing tests for Claude auth diagnostic formatting.
- [x] Add failing tests for `changelog sync` halt-on-auth-failure behavior.
- [x] Implement focused auth-failure detection and actionable Claude guidance.
- [x] Wire sync to halt after the first evaluator auth failure.
- [x] Update troubleshooting docs with the new message and recovery path.
- [x] Run focused tests, Ruff check, and Ruff format check.
