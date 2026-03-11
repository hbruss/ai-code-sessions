# Skill Packaging and Onboarding Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ship the changelog skill bundle inside `ai-code-sessions`, add packaged skill discovery, and upgrade `ais setup` into a self-serve onboarding wizard with prerequisite checks and manual skill-install guidance.

**Architecture:** Vendor the full changelog skill bundle into `src/ai_code_sessions/skills/changelog/`, expose its installed location through a new CLI command, and refactor the existing setup wizard into a broader onboarding flow that separates wrapper CLI choices from changelog evaluator choices. Tighten documentation around scope, prerequisites, and manual skill installation for Codex and Claude.

**Tech Stack:** Python 3.11, Click, questionary, pathlib/importlib.resources, Ruff, pytest

---

> Git commit steps are intentionally omitted from this plan unless the user grants permission for git-modifying commands in the implementation session.

### Task 1: Vendor The Changelog Skill Bundle

**Files:**
- Create: `src/ai_code_sessions/skills/__init__.py`
- Create: `src/ai_code_sessions/skills/changelog/SKILL.md`
- Create: `src/ai_code_sessions/skills/changelog/changelog_utils.py`
- Create: `src/ai_code_sessions/skills/changelog/prime-session.sh`
- Modify: `pyproject.toml`
- Test: `tests/test_cli_setup_web_export_backfill.py`

**Step 1: Copy the source bundle into the package**

Copy the full contents of `/Users/russronchi/.codex/skills/changelog/` into:

- `src/ai_code_sessions/skills/changelog/SKILL.md`
- `src/ai_code_sessions/skills/changelog/changelog_utils.py`
- `src/ai_code_sessions/skills/changelog/prime-session.sh`

Do not delete or modify the source bundle under `~/.codex/skills/changelog/`.

**Step 2: Make the directory a package-backed resource location**

Create:

```python
"""Packaged skill resources for ai-code-sessions."""
```

at `src/ai_code_sessions/skills/__init__.py`.

**Step 3: Ensure package data is included in built artifacts**

Update `pyproject.toml` so the vendored skill files are shipped in wheels and sdists. The implementation may use `tool.uv.build-backend`, `include`, or another packaging mechanism compatible with the current build backend, but the plan outcome is:

- `SKILL.md` included
- `changelog_utils.py` included
- `prime-session.sh` included

**Step 4: Add a basic packaging/resource test**

Add or extend a test so the suite can verify the packaged skill resources exist at runtime.

**Step 5: Run targeted tests**

Run:

```bash
uv run --group dev pytest tests/test_cli_setup_web_export_backfill.py -q
```

Expected: PASS for new packaged-skill assertions.

### Task 2: Add Skill Discovery To The CLI

**Files:**
- Modify: `src/ai_code_sessions/cli.py`
- Modify: `src/ai_code_sessions/core.py`
- Test: `tests/test_cli_flows.py`
- Test: `tests/test_cli_setup_web_export_backfill.py`

**Step 1: Add a helper that resolves the packaged skill path**

Add a helper using package-safe resource access that resolves the installed path for the packaged `changelog` skill bundle.

**Step 2: Add a new CLI surface**

Add a Click command that supports:

```bash
ais skill path changelog
```

Expected behavior:

- prints the resolved packaged bundle path
- exits non-zero for unsupported skill names

**Step 3: Keep output stable and script-friendly**

The command should print only the path on success so it can be used in shell scripts and onboarding examples.

**Step 4: Add CLI tests**

Add tests for:

- successful `changelog` path lookup
- unsupported skill name failure

**Step 5: Run targeted tests**

Run:

```bash
uv run --group dev pytest tests/test_cli_flows.py tests/test_cli_setup_web_export_backfill.py -q
```

Expected: PASS.

### Task 3: Redesign `ais setup` Around Onboarding Choices

**Files:**
- Modify: `src/ai_code_sessions/cli.py`
- Modify: `src/ai_code_sessions/core.py`
- Test: `tests/test_cli_setup_web_export_backfill.py`

**Step 1: Refactor prompt collection**

Change the setup flow so it asks:

- which CLI(s) `ais ctx` should wrap: `codex`, `claude`, `both`
- whether changelog generation is enabled
- if enabled, which evaluator to use: `codex` or `claude`
- evaluator-specific options
- actor
- timezone
- global/repo config choices
- whether `.changelog/` should be committed

**Step 2: Preserve current config-writing behavior**

Continue writing `.ai-code-sessions.toml` and global config with existing overwrite rules and `--force` behavior.

**Step 3: Keep wrapper/evaluator concerns separate**

Do not conflate selected wrapper CLIs with the evaluator choice in config or user-facing output.

**Step 4: Add tests for the new prompt flow**

Update setup tests so they assert:

- wrapper CLI choice is collected separately
- evaluator choice is collected separately
- output/config behavior remains correct

**Step 5: Run targeted tests**

Run:

```bash
uv run --group dev pytest tests/test_cli_setup_web_export_backfill.py -q
```

Expected: PASS.

### Task 4: Add Targeted Prerequisite Checks And Readiness Output

**Files:**
- Modify: `src/ai_code_sessions/cli.py`
- Modify: `src/ai_code_sessions/core.py`
- Test: `tests/test_cli_setup_web_export_backfill.py`
- Test: `tests/test_cli_error_paths.py`

**Step 1: Implement targeted checks**

Add helpers that verify:

- selected wrapper CLIs exist on `PATH`
- selected evaluator CLI exists on `PATH`
- selected CLIs respond to `--version` or `--help`
- repo root exists when repo config is requested
- `jq` exists
- `rg` exists
- `fd` exists optionally
- `gh` exists optionally

**Step 2: Classify results**

Represent outcomes as:

- `PASS`
- `WARN`
- `FAIL`

Required checks for the chosen workflow should be `FAIL` when missing. Optional checks should produce `WARN`.

**Step 3: Print an onboarding summary**

After checks run, print:

- what was checked
- what passed
- what is degraded
- what blocks the selected workflow

Use exact tool names and consequences in the output.

**Step 4: Add tests**

Add coverage for:

- success path
- missing required CLI
- missing `jq`
- missing `rg`
- optional-tool warnings

**Step 5: Run targeted tests**

Run:

```bash
uv run --group dev pytest tests/test_cli_setup_web_export_backfill.py tests/test_cli_error_paths.py -q
```

Expected: PASS.

### Task 5: Print Manual Skill Installation Instructions

**Files:**
- Modify: `src/ai_code_sessions/cli.py`
- Modify: `src/ai_code_sessions/core.py`
- Test: `tests/test_cli_setup_web_export_backfill.py`

**Step 1: Use the packaged bundle path in setup output**

Reuse the same packaged-skill path helper from `ais skill path changelog`.

**Step 2: Print exact copy commands**

Print exact manual install commands for:

- user-wide Codex: `~/.codex/skills/changelog/`
- project-local Codex: `<repo>/.codex/skills/changelog/`
- user-wide Claude: `~/.claude/skills/changelog/`
- project-local Claude: `<repo>/.claude/skills/changelog/`

Tailor setup output to the user’s chosen wrapper CLIs and evaluator, but keep the docs comprehensive.

**Step 3: Print verification commands**

Include a brief “verify it worked” section in setup output, for example by checking that the target directory contains `SKILL.md`.

**Step 4: Add tests**

Assert that setup output includes:

- packaged bundle path
- relevant install targets
- clear distinction between user-wide and project-local installation

**Step 5: Run targeted tests**

Run:

```bash
uv run --group dev pytest tests/test_cli_setup_web_export_backfill.py -q
```

Expected: PASS.

### Task 6: Tighten Documentation For Self-Serve Onboarding

**Files:**
- Create: `docs/skills.md`
- Modify: `README.md`
- Modify: `docs/README.md`
- Modify: `docs/cli.md`
- Modify: `docs/config.md`
- Modify: `docs/changelog.md`
- Modify: `docs/troubleshooting.md`

**Step 1: Add a dedicated skills document**

Create `docs/skills.md` covering:

- what the shipped changelog skill is
- bundle contents
- prerequisite tools
- user-wide vs project-local install
- Codex vs Claude target paths
- exact copy commands
- verification steps

**Step 2: Update onboarding references**

Update `README.md` and `docs/README.md` so they:

- mention the shipped skill bundle
- link to `docs/skills.md`
- explain that `ais setup` is now an onboarding wizard

**Step 3: Update command/config docs**

Update `docs/cli.md` and `docs/config.md` to explain:

- wrapper CLI choice vs evaluator choice
- config scope vs skill-install scope
- `ais skill path changelog`

**Step 4: Update changelog/troubleshooting docs**

Document:

- when the skill matters
- missing `jq` / `rg`
- wrong install scope
- wrong target directory

**Step 5: Run doc-focused checks**

Run:

```bash
uv run --group dev pytest tests/test_cli_flows.py tests/test_cli_setup_web_export_backfill.py -q
```

Expected: PASS for docs-adjacent command coverage.

### Task 7: Run Full Verification

**Files:**
- Verify: `src/ai_code_sessions/cli.py`
- Verify: `src/ai_code_sessions/core.py`
- Verify: `src/ai_code_sessions/skills/changelog/*`
- Verify: `docs/skills.md`
- Verify: `README.md`
- Verify: `docs/*.md`
- Verify: `tests/*.py`

**Step 1: Lint changed Python files**

Run:

```bash
uv run --group dev ruff check --fix src/ai_code_sessions/cli.py src/ai_code_sessions/core.py tests/test_cli_flows.py tests/test_cli_setup_web_export_backfill.py tests/test_cli_error_paths.py
```

Expected: PASS with no remaining lint errors.

**Step 2: Format changed Python files**

Run:

```bash
uv run --group dev ruff format src/ai_code_sessions/cli.py src/ai_code_sessions/core.py tests/test_cli_flows.py tests/test_cli_setup_web_export_backfill.py tests/test_cli_error_paths.py
```

Expected: PASS.

**Step 3: Run the full test suite**

Run:

```bash
uv run --group dev pytest -q
```

Expected: full suite PASS.

**Step 4: Smoke-check the new discovery command**

Run:

```bash
uv run --project . ais skill path changelog
```

Expected: prints the packaged bundle path and exits 0.

**Step 5: Smoke-check setup output**

Run:

```bash
uv run --project . ais setup --help
```

Expected: help text reflects onboarding behavior and new skill guidance entry points.
