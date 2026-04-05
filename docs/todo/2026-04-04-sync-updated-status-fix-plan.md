# Sync Updated Status Fix Plan (TDD)

- [x] Identify current sync behavior in `core.py` and `cli.py` for `exists` vs `updated`.
- [x] Add focused failing tests:
- [x] unchanged sync-owned existing row returns `exists` and does not invoke evaluator
- [x] changed `end` on sync-owned row updates existing row
- [x] dry-run sync prints `would update existing`
- [x] real sync treats `updated` as processed success
- [x] Implement minimal code fix in allowed files only.
- [x] Re-run focused tests until green.
- [x] Run Ruff check + format check on touched files.
- [x] Summarize outcomes and any concerns.
