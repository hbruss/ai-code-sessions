# Codex Sync Long-Running Session Plan

**Goal:** Make `ais changelog sync --codex` discover Codex rollouts that started before the default scan window but received final events inside the window.

## Checklist

- [x] Reproduce the Frank-Eileen symptom and identify whether it is parser drift, repo resolution, or discovery-window logic.
- [x] Add a regression test for a Codex rollout stored in an older start-day directory whose last event overlaps the requested window.
- [x] Update Codex native-session discovery so scoped sync sees recently updated older rollout files without broadening repo matching rules.
- [x] Run focused pytest coverage, Ruff checks, and a Frank-Eileen dry-run proof.
