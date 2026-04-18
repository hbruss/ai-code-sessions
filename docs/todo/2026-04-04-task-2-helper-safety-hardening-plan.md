# Task 2 Helper Safety Hardening Plan (2026-04-04)

- [x] Add direct helper tests for archive symlink-escape rejection.
- [x] Add direct helper tests for cleanup path contract rejection (non-repo + non-temp).
- [x] Keep cleanup idempotence test for valid repo temp artifacts.
- [x] Run targeted tests first and confirm they fail (red).
- [x] Implement minimal helper-only fixes in `src/ai_code_sessions/core.py`.
- [x] Re-run targeted tests and confirm they pass (green).
- [x] Run full `tests/test_changelog_evaluator_subprocess.py`.
- [x] Run Ruff check on `src/ai_code_sessions/core.py` and `tests/test_changelog_evaluator_subprocess.py`.
