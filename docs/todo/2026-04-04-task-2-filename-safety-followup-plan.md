# Task 2 Follow-Up: Filename Safety Gate Coverage

## Scope
- Add missing test coverage for `_validated_changelog_temp_artifact_path` filename validation behavior used by:
- `_archive_failed_changelog_prompt`
- `_cleanup_changelog_prompt_artifact`

## TDD Plan
- [x] Add focused tests for “right directory, wrong filename” in `tests/test_changelog_evaluator_subprocess.py`.
- [x] Run only the new tests and confirm they fail (RED).
- [x] Apply the smallest possible fix in `src/ai_code_sessions/core.py` only if needed.
- [x] Run only the new tests and confirm they pass (GREEN).
- [x] Run full `tests/test_changelog_evaluator_subprocess.py`.
- [x] Run Ruff check on the two owned files.

## Notes
- Keep changes limited to owned files.
- Use malformed filename under `<project_root>/.tmp/changelog-eval/` to prove both archive and cleanup safety contracts.
