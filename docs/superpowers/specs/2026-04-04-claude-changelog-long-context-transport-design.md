# Claude Changelog Long-Context Transport Design

Status: draft for review

Last updated: 2026-04-04 PDT

## Problem

`ai-code-sessions` currently runs Claude changelog evaluation headlessly by passing the entire prompt as a positional `claude --print` argument. That has two problems:

1. Very large changelog prompts can fail before Claude sees them because shell / OS argv limits are much smaller than Claude’s model context limits.
2. Claude budget fallback is currently triggered for a mixed set of causes:
   - genuine model context pressure
   - timeout
   - prompt transport failure such as `argument list too long`

This means Claude’s newly available 1M context models cannot be fully exploited by the current evaluator path.

## Goals

- Allow Claude changelog evaluation to use long-context models such as `opus[1m]`.
- Remove argv size as the primary bottleneck for large changelog prompts.
- Preserve repo isolation and avoid any possibility of cross-pollinating changelog artifacts between repos.
- Preserve full failed prompts for debugging.
- Keep the current budget digest path as a fallback rather than removing it.
- Make the evaluator’s transport and fallback behavior observable and debuggable.

## Non-Goals

- No changes to changelog schema or changelog storage layout.
- No multi-repo orchestration in this work.
- No removal of budget fallback entirely.
- No persistent prompt storage in `.changelog/`.
- No silent compatibility layer that hides whether the full or budget prompt was used.

## Constraints

- Scratch artifacts must live in the target repo’s `.tmp/` directory, not in `.changelog/`.
- Failure artifacts should be retained for debugging and moved to `.archive/` when appropriate.
- Git write operations remain explicit and separate from this design.
- Safety matters more than convenience. Any design that risks cross-repo mixing is unacceptable.

## Current Behavior

The current Claude evaluator path in `src/ai_code_sessions/core.py`:

- builds a full prompt from the changelog digest
- invokes `claude --print`
- passes the prompt as the final positional command-line argument
- retries with a budget digest on timeout or context-like failure

This is directionally correct but transport-limited.

## Options Considered

### Option 1: Only switch Claude to `opus[1m]`

Pros:
- minimal code change
- likely improves some medium-large sessions

Cons:
- does not solve argv transport limits
- does not eliminate `argument list too long`
- still conflates transport failure with actual model capacity

Decision: reject

### Option 2: File-backed prompt artifact plus direct stdin delivery

Pros:
- removes argv as the transport bottleneck
- preserves full prompt for debugging
- keeps repo-local isolation

Cons:
- depends on validating Claude Code headless stdin behavior

Decision: viable, but should be implemented behind a validation gate

### Option 3: Transport abstraction plus staged rollout

Pros:
- separates model selection from transport mechanism
- safest rollout path
- easiest to test and reason about
- retains budget fallback cleanly

Cons:
- slightly more implementation work

Decision: chosen

## Chosen Design

The Claude changelog evaluator should move to a file-backed, non-argv prompt transport with explicit artifact lifecycle management.

### Full-Prompt Flow

1. Build the full digest exactly as today.
2. Build the full evaluator prompt exactly as today.
3. Write the prompt to a repo-local scratch artifact:

```text
<project_root>/.tmp/changelog-eval/<run_id>-full-prompt.txt
```

4. Invoke Claude Code headlessly with:
   - `--print`
   - `--no-session-persistence`
   - `--output-format json`
   - `--json-schema ...`
   - `--strict-mcp-config`
   - empty `--mcp-config`
   - `--permission-mode dontAsk`
   - `--tools ""`
   - `--model opus[1m]` when Claude is selected and no explicit model override is set
   - prompt delivered through a validated non-argv path

5. If the full run succeeds:
   - parse structured output as today
   - clean up or archive the prompt artifact according to the retention rules below

### Fallback Flow

If the full run fails due to timeout, transport pressure, or context-related failure:

1. Preserve the full prompt artifact for debugging.
2. Build the budget digest.
3. Build a second prompt artifact:

```text
<project_root>/.tmp/changelog-eval/<run_id>-budget-prompt.txt
```

4. Retry once with the budget prompt.
5. If the budget run succeeds:
   - return structured output normally
   - preserve artifacts according to failure/debug retention policy
6. If the budget run fails:
   - preserve both artifacts
   - write the existing changelog failure record
   - expose the failure mode clearly

## Model Selection

For the Claude evaluator only:

- if the user explicitly sets a model, use it unchanged
- otherwise default to `opus[1m]`

Rationale:
- the user already has Max and wants long-context Claude via CLI subscription
- the point of this change is to exploit the larger context window when available

This default should only affect the Claude changelog evaluator path, not unrelated Claude workflows.

## Prompt Artifact Lifecycle

### Success

Preferred behavior:
- remove transient prompt artifacts from `.tmp/` after successful evaluation

Reason:
- successful prompt artifacts are primarily transport intermediates
- retaining all of them indefinitely would create unnecessary sensitive-data sprawl

### Failure

Required behavior:
- preserve prompt artifacts for debugging
- move them into:

```text
<project_root>/.archive/changelog-eval/
```

Suggested filenames:

```text
<run_id>-full-prompt.txt
<run_id>-budget-prompt.txt
```

Reason:
- failed runs are exactly when the prompt body is useful for diagnosis
- `.archive/` is the correct durable location under repo policy

## Observability

The evaluator path should emit enough signal to distinguish:

- full prompt used successfully
- budget fallback used after failure
- failure caused by timeout
- failure caused by transport pressure such as `argument list too long`
- failure caused by model/context constraints

At minimum, logs or user-facing status should include:

- evaluator: `claude`
- model used
- prompt mode: `full` or `budget`
- whether fallback happened
- artifact path(s) retained on failure

## Transport Validation Gate

This design intentionally does not assume that Claude Code’s exact non-argv prompt transport is already proven.

Before changing production behavior, the implementation must validate:

1. Claude Code accepts the selected non-argv transport in headless `--print` mode.
2. Structured output still works through that transport.
3. Large prompts no longer fail with argv-size errors.
4. Timeout and budget fallback still work correctly.

If the preferred transport is not sufficiently reliable, stop and choose the next safest transport rather than shipping a guessed integration.

## Testing Strategy

### Unit Tests

- prompt artifact path creation is repo-local and deterministic
- success path cleans up transient `.tmp/` artifacts
- failure path preserves and archives prompt artifacts
- explicit model override still wins over the new default
- Claude default model becomes `opus[1m]` only when no explicit model is supplied
- budget retry still occurs once on timeout or context/transport failure

### Integration-Like Tests

- fake Claude runner receives prompt via the new transport path, not positional argv
- archived failure artifacts contain the expected full and budget prompt text
- repo-local paths are used even when multiple project roots are exercised in tests

### Manual Validation

- run a known small session with Claude and confirm no behavior regression
- run a known large session that previously hit argv/context pressure
- confirm the full prompt path is attempted first
- confirm fallback behavior is preserved

## Safety Review

This design is safe relative to the current repo policy because:

- artifacts are scoped to the target repo root
- artifacts are not written into `.changelog/`
- successful runs do not accumulate debug files indefinitely
- failed runs preserve enough evidence to debug without mutating changelog content beyond the existing flow

The main remaining risk is transport implementation correctness, which is why the validation gate is part of the design rather than an afterthought.

## Implementation Outline

1. Introduce a small prompt-artifact helper for changelog evaluation.
2. Introduce a Claude prompt transport helper that does not rely on positional argv payloads.
3. Change the Claude default model to `opus[1m]` when unset.
4. Wire artifact retention into success/failure paths.
5. Keep budget fallback behavior, but reclassify transport failures explicitly.
6. Add focused tests before broader regression verification.

## Open Question

The final implementation must select the safest verified non-argv transport for Claude Code `--print`.

This is an implementation validation question, not a product-design question:

- prefer stdin if validated
- otherwise use the next-most-reliable supported mechanism

The design does not depend on which one wins, as long as it removes large prompt payloads from argv and preserves the same structured-output contract.
