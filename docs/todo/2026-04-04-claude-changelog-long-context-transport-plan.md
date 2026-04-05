# Claude Changelog Long-Context Transport Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable Claude changelog evaluation to use long-context models safely by removing large prompt payloads from argv, preserving failed prompt artifacts for debugging, and keeping budget fallback as a last resort.

**Architecture:** Add a small prompt-artifact and prompt-transport layer around the existing Claude evaluator, validate a non-argv delivery path first, then wire repo-local `.tmp/` and `.archive/` lifecycle management into the full-prompt and budget-fallback flow. Keep changelog schema and repo targeting unchanged.

**Tech Stack:** Python 3.11, Click, subprocess, pathlib, pytest, Ruff, Claude Code CLI

**Implementation record (2026-04-04):** This plan is now implemented. Any `Expected: fail ...` lines below are retained as historical red-phase TDD notes, not current repo behavior.

---

### Task 1: Prove the Claude transport can avoid argv safely

**Files:**
- Modify: `src/ai_code_sessions/core.py`
- Modify: `tests/test_changelog_evaluator_subprocess.py`

- [x] **Step 1: Write the failing subprocess tests for non-argv prompt transport**

Add focused tests in `tests/test_changelog_evaluator_subprocess.py` that lock in:

```python
def test_claude_evaluator_does_not_pass_prompt_in_argv(monkeypatch, tmp_path):
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        seen["input"] = kwargs.get("input")
        return CompletedProcess(args=args, returncode=0, stdout='{"structured_output":{"summary":"ok"},"is_error":false}', stderr="")

    monkeypatch.setattr(core.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(core.subprocess, "run", fake_run)

    prompt = "very large prompt body"
    result = core._run_claude_changelog_evaluator(
        prompt=prompt,
        json_schema={},
        cd=tmp_path,
        model="opus[1m]",
    )

    assert result == {"summary": "ok"}
    assert prompt not in seen["args"]
    assert seen["input"] == prompt
```

```python
def test_claude_evaluator_defaults_to_opus_1m(monkeypatch):
    seen = {}

    def fake_run(args, **kwargs):
        seen["args"] = args
        return CompletedProcess(args=args, returncode=0, stdout='{"structured_output":{"summary":"ok"},"is_error":false}', stderr="")

    monkeypatch.setattr(core.shutil, "which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(core.subprocess, "run", fake_run)

    core._run_claude_changelog_evaluator(prompt="hi", json_schema={})

    assert "--model" in seen["args"]
    assert seen["args"][seen["args"].index("--model") + 1] == "opus[1m]"
```

Run:

```bash
uv run --group dev pytest tests/test_changelog_evaluator_subprocess.py -q -k "does_not_pass_prompt_in_argv or defaults_to_opus_1m"
```

Expected (historical red phase): fail because the evaluator at that time still appended the prompt to argv and defaulted to `opus`.

- [x] **Step 2: Implement the validated non-argv Claude transport**

Update `src/ai_code_sessions/core.py` so `_run_claude_changelog_evaluator(...)`:

- defaults Claude model to `opus[1m]` when `model` is unset
- keeps all existing headless flags
- removes the positional prompt argument from `args`
- calls `subprocess.run(..., input=prompt, text=True, capture_output=True, timeout=timeout_seconds)`

Shape:

```python
args = [
    exe,
    "--print",
    "--no-session-persistence",
    "--output-format",
    "json",
    "--json-schema",
    json.dumps(json_schema, ensure_ascii=False),
    "--strict-mcp-config",
    "--mcp-config",
    json.dumps({"mcpServers": {}}, ensure_ascii=False),
    "--permission-mode",
    "dontAsk",
    "--tools",
    "",
    "--model",
    model,
    "--max-thinking-tokens",
    str(max_thinking_tokens),
]

proc = subprocess.run(
    args,
    cwd=str(cd) if cd else None,
    input=prompt,
    text=True,
    capture_output=True,
    timeout=timeout_seconds,
)
```

Do not change structured-output parsing yet.

- [x] **Step 3: Run the focused subprocess test file**

Run:

```bash
uv run --group dev pytest tests/test_changelog_evaluator_subprocess.py -q
```

Expected: pass for the new transport/default-model assertions and preserve existing structured-output cases.

### Task 2: Add repo-local prompt artifact helpers with explicit lifecycle

**Files:**
- Modify: `src/ai_code_sessions/core.py`
- Modify: `tests/test_changelog_evaluator_subprocess.py`

- [x] **Step 1: Write failing tests for prompt artifact creation and archival**

Add tests that lock in:

```python
def test_changelog_prompt_artifact_paths_are_repo_local(tmp_path):
    full_path = core._changelog_prompt_artifact_path(
        project_root=tmp_path,
        run_id="run-123",
        variant="full",
    )
    assert full_path == tmp_path / ".tmp" / "changelog-eval" / "run-123-full-prompt.txt"
```

```python
def test_archive_failed_changelog_prompt_moves_to_archive(tmp_path):
    src = tmp_path / ".tmp" / "changelog-eval" / "run-123-full-prompt.txt"
    src.parent.mkdir(parents=True)
    src.write_text("prompt", encoding="utf-8")

    archived = core._archive_failed_changelog_prompt(project_root=tmp_path, prompt_path=src)

    assert archived == tmp_path / ".archive" / "changelog-eval" / "run-123-full-prompt.txt"
    assert archived.read_text(encoding="utf-8") == "prompt"
    assert not src.exists()
```

Run:

```bash
uv run --group dev pytest tests/test_changelog_evaluator_subprocess.py -q -k "artifact_paths_are_repo_local or archive_failed_changelog_prompt"
```

Expected (historical red phase): fail because the helpers did not exist yet.

- [x] **Step 2: Implement prompt artifact helpers in `core.py`**

Add small helpers near the evaluator code:

```python
def _changelog_prompt_artifact_path(*, project_root: Path, run_id: str, variant: str) -> Path:
    return project_root / ".tmp" / "changelog-eval" / f"{run_id}-{variant}-prompt.txt"


def _write_changelog_prompt_artifact(*, project_root: Path, run_id: str, variant: str, prompt: str) -> Path:
    path = _changelog_prompt_artifact_path(project_root=project_root, run_id=run_id, variant=variant)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(prompt, encoding="utf-8")
    return path


def _archive_failed_changelog_prompt(*, project_root: Path, prompt_path: Path) -> Path:
    archive_dir = project_root / ".archive" / "changelog-eval"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived = archive_dir / prompt_path.name
    shutil.move(str(prompt_path), str(archived))
    return archived
```

Add a small success-path cleanup helper too:

```python
def _cleanup_changelog_prompt_artifact(prompt_path: Path | None) -> None:
    if prompt_path and prompt_path.exists():
        prompt_path.unlink()
```

- [x] **Step 3: Run the helper-focused tests**

Run:

```bash
uv run --group dev pytest tests/test_changelog_evaluator_subprocess.py -q -k "artifact_paths_are_repo_local or archive_failed_changelog_prompt"
```

Expected: pass.

### Task 3: Wire full-prompt artifacts and budget fallback into the changelog flow

**Files:**
- Modify: `src/ai_code_sessions/core.py`
- Modify: `tests/test_changelog_evaluator_errors.py`

- [x] **Step 1: Write failing tests for preserved full/budget prompt artifacts on failure**

Add tests in `tests/test_changelog_evaluator_errors.py` that monkeypatch the evaluator path and assert:

```python
def test_generate_and_append_preserves_full_prompt_and_retries_budget_on_timeout(...):
    ...
    assert (project_root / ".archive" / "changelog-eval" / f"{run_id}-full-prompt.txt").exists()
    assert calls == ["full", "budget"]
```

```python
def test_generate_and_append_cleans_up_full_prompt_on_success(...):
    ...
    assert not (project_root / ".tmp" / "changelog-eval" / f"{run_id}-full-prompt.txt").exists()
```

Run:

```bash
uv run --group dev pytest tests/test_changelog_evaluator_errors.py -q
```

Expected (historical red phase): fail because the main changelog flow did not manage prompt artifacts yet.

- [x] **Step 2: Update `_generate_and_append_changelog_entry(...)` to use the artifact lifecycle**

Inside the evaluator block:

1. compute `prompt = _build_codex_changelog_prompt(digest=d)`
2. write the full prompt artifact before the first Claude eval
3. on success, clean up the full prompt artifact
4. on timeout/context/transport fallback:
   - archive the full prompt artifact
   - build budget digest
   - write a budget prompt artifact
   - retry once
5. if budget succeeds after failure:
   - clean up the budget temp artifact
6. if budget fails:
   - archive whichever prompt artifacts still exist
   - write the normal failure record

Do not change the entry schema.

- [x] **Step 3: Expand context-like failure detection carefully**

Ensure `_looks_like_context_window_error(...)` still catches:

- `argument list too long`
- prompt too long
- context too long

Add one more recognized bucket only if the validated Claude transport introduces a new deterministic large-input error string. Do not guess at undocumented strings.

- [x] **Step 4: Run the error-handling tests**

Run:

```bash
uv run --group dev pytest tests/test_changelog_evaluator_errors.py -q
```

Expected: pass for success cleanup and failure archival behavior.

### Task 4: Update configuration/defaults and document the new Claude behavior

**Files:**
- Modify: `docs/changelog.md`
- Modify: `docs/config.md`
- Modify: `tests/test_cli_changelog.py`
- Modify: `src/ai_code_sessions/core.py`

- [x] **Step 1: Write failing tests or assertions for Claude default model behavior**

Add a focused test in `tests/test_cli_changelog.py` or the subprocess evaluator test file that verifies:

- explicit `--model` still wins
- unset Claude model uses `opus[1m]`

Run:

```bash
uv run --group dev pytest tests/test_changelog_evaluator_subprocess.py tests/test_cli_changelog.py -q -k "opus_1m or explicit model"
```

Expected (historical red phase): fail until the default model behavior was fully reflected in the relevant code path(s).

- [x] **Step 2: Update user-facing docs**

In `docs/changelog.md`, document:

- Claude changelog evaluation now defaults to `opus[1m]` when no model override is provided
- large prompts are delivered through a non-argv transport
- failed prompt artifacts are preserved under `.archive/changelog-eval/`
- budget mode remains a fallback

In `docs/config.md`, document the model override behavior clearly:

```toml
[changelog]
evaluator = "claude"
model = "opus[1m]"  # optional; blank uses the Claude long-context default
```

- [x] **Step 3: Run the docs-adjacent targeted tests**

Run:

```bash
uv run --group dev pytest tests/test_changelog_evaluator_subprocess.py tests/test_cli_changelog.py -q
```

Expected: pass.

### Task 5: Final verification sweep

**Files:**
- Modify: none expected

- [x] **Step 1: Run the focused Python test suite**

Run:

```bash
uv run --group dev pytest tests/test_changelog_evaluator_subprocess.py tests/test_changelog_evaluator_errors.py tests/test_cli_changelog.py -q
```

Expected: all pass.

- [x] **Step 2: Run broader changelog regression coverage**

Run:

```bash
uv run --group dev pytest tests/test_changelog_sync.py tests/test_changelog_validation.py tests/test_changelog_usage_limit_detection.py -q
```

Expected: all pass.

- [x] **Step 3: Run Ruff check**

Run:

```bash
uv run --group dev ruff check src/ai_code_sessions/core.py tests/test_changelog_evaluator_subprocess.py tests/test_changelog_evaluator_errors.py tests/test_cli_changelog.py
```

Expected: `All checks passed!`

- [x] **Step 4: Run Ruff format check**

Run:

```bash
uv run --group dev ruff format --check src/ai_code_sessions/core.py tests/test_changelog_evaluator_subprocess.py tests/test_changelog_evaluator_errors.py tests/test_cli_changelog.py
```

Expected: all files already formatted

- [ ] **Step 5: Manual smoke test notes**

Capture the manual validation checklist for the execution session:

- small Claude changelog eval succeeds with full prompt
- large Claude changelog eval attempts full prompt first
- fallback path preserves archived prompt artifacts on failure
- explicit Claude model override still works

Note: do not claim the manual smoke test is complete until it is actually run in the execution session.

## Self-Review

- Spec coverage: transport validation, repo-local `.tmp` storage, `.archive` retention on failure, `opus[1m]` default, observability, and budget fallback all have concrete tasks above.
- Placeholder scan: no `TODO` / `TBD` placeholders remain; each task names exact files and commands.
- Type consistency: all helpers and file paths referenced in later tasks are introduced before they are used.

## Execution Handoff

Plan complete and saved to `docs/todo/2026-04-04-claude-changelog-long-context-transport-plan.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?

## Implementation Record

- 2026-04-04 PDT: User-facing docs follow-through for this shipped Claude transport work was completed during the later native-sync subagent-exclusion documentation pass (README + changelog/config/repair docs updated together for consistency).
