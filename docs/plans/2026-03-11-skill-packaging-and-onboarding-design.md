# Skill Packaging and Onboarding Design

**Date:** 2026-03-11

## Goal

Ship the changelog skill bundle inside `ai-code-sessions`, keep skill installation manual, and expand `ais setup` into a self-serve onboarding wizard that verifies prerequisites, explains config scope, and prints exact manual install steps for both Codex CLI and Claude Code CLI.

## User Constraints

- Copy the skill bundle from `/Users/russronchi/.codex/skills/changelog/`.
- Do not remove or modify the existing source bundle at `/Users/russronchi/.codex/skills/changelog/`.
- Include the full bundle, not a phased subset.
- Keep skill installation manual, but make the guidance detailed enough that a collaborator can complete it without hand-holding.

## Problem

Today the repository does not ship the changelog skill bundle at all, and `ais setup` is only a configuration wizard. That leaves a new user with multiple gaps:

- no packaged skill files
- no documented difference between user-wide and project-local skill installation
- no documented difference between Codex and Claude skill locations
- no clear separation between the CLI used for `ais ctx` and the CLI used for changelog evaluation
- no prerequisite verification for the chosen workflow
- no clear readiness summary after setup

This is workable for an expert user, but it is not yet a strong onboarding experience for a collaborator installing the tool for the first time.

## Approved Product Direction

### 1. Ship a canonical changelog skill bundle inside the package

The repo will vendor the full bundle from `/Users/russronchi/.codex/skills/changelog/` into the package as canonical packaged assets. The expected bundle contents are:

- `SKILL.md`
- `changelog_utils.py`
- `prime-session.sh`

The original bundle under `~/.codex/skills/changelog/` remains in place and unchanged. The repo-owned copy becomes the installable distribution source for end users.

### 2. Keep skill installation manual

`ai-code-sessions` will not auto-copy or auto-symlink the skill into Codex or Claude directories. Instead, the tool will:

- expose the packaged bundle path via a small discovery command
- document all supported install targets
- print exact manual install commands during onboarding

This keeps installation explicit and debuggable while still making the process self-guided.

### 3. Expand `ais setup` into a broader onboarding wizard

`ais setup` will become the first-run onboarding flow, not just a config writer. It will:

- gather usage choices
- perform targeted prerequisite checks
- write config
- explain the packaged skill bundle and manual installation options
- summarize next steps and readiness

## Decisions

### Separate session wrapper choice from changelog evaluator choice

The onboarding wizard will treat these as distinct decisions:

1. Which CLI(s) should `ais ctx` wrap: `codex`, `claude`, or both?
2. Should changelog generation be enabled?
3. If enabled, which evaluator should generate changelog entries: `codex` or `claude`?

No default wrapped assistant is required beyond the existing explicit `--codex` / `--claude` invocation model.

### Add a skill discovery command

The CLI will add a small discovery command:

```bash
ais skill path changelog
```

Its job is to print the packaged bundle path for the shipped changelog skill. This gives both documentation and the onboarding wizard one stable mechanism for telling users where the bundle lives after installation.

### Support user-wide and project-local skill placement for both tools

The docs and onboarding output will explain four manual targets:

- Codex user-wide: `~/.codex/skills/changelog/`
- Codex project-local: `<repo>/.codex/skills/changelog/`
- Claude user-wide: `~/.claude/skills/changelog/`
- Claude project-local: `<repo>/.claude/skills/changelog/`

The onboarding wizard will tailor examples to the user’s selected CLIs, but the docs will show all four locations clearly.

### Keep setup non-destructive

The onboarding wizard will check and explain, but it will not install the skill automatically. It also will not mutate the source bundle under `~/.codex/skills/changelog/`.

## Proposed Package Layout

The packaged bundle should live inside the Python package so it is available from installed wheels and sdists:

```text
src/ai_code_sessions/
├── skills/
│   ├── __init__.py
│   └── changelog/
│       ├── SKILL.md
│       ├── changelog_utils.py
│       └── prime-session.sh
```

The implementation should access these files through `importlib.resources` or an equivalent package-data-safe mechanism. The design should not rely on source-tree-relative paths that break after installation.

## Onboarding Wizard Design

### Prompt flow

The updated `ais setup` flow should look like this:

1. Intro text explaining that setup covers:
   - `ais ctx` configuration
   - changelog evaluation setup
   - prerequisite checks
   - config scope
   - manual skill installation guidance
2. Ask which CLI(s) the user wants `ais ctx` to wrap:
   - `codex`
   - `claude`
   - `both`
3. Ask whether changelog generation should be enabled.
4. If enabled, ask which evaluator should generate changelog entries:
   - `codex`
   - `claude`
5. Ask evaluator-specific options:
   - `codex`: optional model override
   - `claude`: optional model override and thinking-token budget
6. Ask for actor and timezone.
7. Ask config scope:
   - write global config
   - write repo config
8. Ask whether `.changelog/` should be committed.
9. Run prerequisite checks for the selected workflow.
10. Show results.
11. Write config.
12. Print exact manual skill install commands and next steps.

### Preflight checks

Checks should be driven by the user’s actual choices, not a blanket global checklist.

Required checks:

- selected wrapper CLI binaries are available on `PATH`
- selected evaluator CLI is available on `PATH`
- selected CLI can respond to a harmless diagnostic command such as `--version` or `--help`
- repo root is valid when repo config is requested
- helper tools required by the changelog skill are present:
  - `jq`
  - `rg`

Optional checks:

- `fd` available for nicer helper-script behavior
- `gh` available for gist-related workflows
- actor auto-detection result if changelog is enabled

The output must distinguish:

- `PASS`: confirmed working
- `WARN`: not required or partially degraded
- `FAIL`: required for the selected workflow and missing

### Results and guidance

The final setup output should tell the user:

- what was configured
- what passed
- what needs fixing
- where the packaged skill bundle lives
- exactly how to copy it into the chosen install scope
- how to verify the installation
- what command to run next

## Documentation Design

### New doc

Add `docs/skills.md` covering:

- what the changelog skill is
- the files included in the shipped bundle
- why installation is manual
- user-wide vs project-local installation
- Codex vs Claude installation locations
- exact copy commands
- verification steps
- prerequisites for helper scripts: `jq`, `rg`, optional `fd`

### Updated docs

Update:

- `README.md`
- `docs/cli.md`
- `docs/config.md`
- `docs/changelog.md`
- `docs/troubleshooting.md`
- `docs/README.md`

These docs should explicitly separate:

- config scope vs skill-install scope
- wrapper CLI choice vs changelog evaluator choice
- package-shipped skill bundle vs manual installation target

## Testing and Verification Design

### Automated tests

Add tests for:

- packaged skill files included in install artifacts or package resources
- `ais skill path changelog`
- updated `ais setup` prompt flow
- targeted prerequisite checks and summarized results
- manual install instructions for Codex and Claude targets
- docs/examples tied to actual command names and actual paths

### Manual smoke test

A release smoke test should verify:

1. install the package into a fresh environment
2. run `ais skill path changelog`
3. run `ais setup`
4. follow the printed manual install instructions
5. confirm a collaborator can reach a working `ais ctx` and changelog setup without outside help

## Non-Goals

- no automatic skill installation
- no symlink manager
- no hidden mutation of `~/.codex/skills/changelog/`
- no phased rollout that omits parts of the skill bundle

## Risks

- package-data misconfiguration could ship docs/templates but omit the skill bundle
- helper scripts assume POSIX shell plus `jq` and `rg`, which may need stronger warnings for cross-platform users
- a broader setup wizard can become noisy if checks are not targeted to the user’s choices

## Recommendation

Implement the full approved scope in one pass:

- vendor the complete skill bundle into the package
- expose it with `ais skill path changelog`
- expand `ais setup` into a real onboarding wizard
- tighten docs so a first-time collaborator can self-serve the entire install

This is the right tradeoff for a pre-userbase product: one clear canonical flow, no compatibility bridge, and no artificial staging.
