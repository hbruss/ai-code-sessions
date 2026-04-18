# Task 2 Final Safety Gap Plan

- [x] Add a blocking archive-flow test that rejects nested descendants under `.tmp/changelog-eval/`.
- [x] Add a blocking cleanup-flow test that rejects nested descendants under `.tmp/changelog-eval/`.
- [x] Run only the two new tests to confirm they fail first (red).
- [x] Apply the minimal helper validation change so only direct children are allowed.
- [x] Re-run focused tests (green), then run full subprocess evaluator test file, then Ruff check.
