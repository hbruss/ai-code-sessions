# Native Session Repair Dry-Run Verification Plan

**Goal:** Validate the new `ais changelog repair-native-sync` grouping and reporting against real downstream changelog data in dry-run mode only, and decide whether apply mode appears safe enough to recommend.

**Constraints:**
- Do not modify external changelog repos or files.
- Do not run `--apply`.
- Do not perform git write operations.
- Focus on real downstream repos already known to exhibit the duplicate pattern.

## Steps

- [x] Reconfirm the local implementation and docs so the verification criteria are explicit.
- [x] Locate the downstream repo roots and confirm each target has `.changelog` data to inspect.
- [x] Run `ais changelog repair-native-sync --project-root <repo>` in dry-run mode for selected repos.
- [x] Capture the reported `AUTO`, `MANUAL`, and `SKIP` groups for each repo.
- [x] Compare the dry-run output against the intended conservative repair policy.
- [x] Summarize whether apply mode looks safe now, and call out any gaps or output limitations before recommending it.
