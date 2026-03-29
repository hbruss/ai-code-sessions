# Native Sync Bakeoff Plan

> Goal: validate `ais changelog sync` against real historical sessions in an isolated clone, compare new output to existing changelog entries, and adjust the implementation until the behavior and output quality are acceptable.

- [ ] Create an isolated clone under `.worktree/` that includes the current uncommitted working tree state
- [ ] Select a representative real-session corpus from this repo's existing changelog + transcript/native artifacts
- [ ] Build a scratch native-session home in the clone so `sync` can replay the corpus without touching the real home directories or real changelog
- [ ] Run `ais changelog sync` in dry-run and real modes inside the clone
- [ ] Compare generated entries to existing entries for coverage, repo targeting, duplicate behavior, labels, summaries, bullets, tags, and derived metadata
- [ ] Adjust implementation if the bakeoff exposes real regressions or quality gaps
- [ ] Re-run the bakeoff until the result is acceptable
- [ ] Summarize outcomes, evidence, and any remaining caveats
