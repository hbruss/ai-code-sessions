# AI Lessons

## 2026-03-01

- Don’t assume “Claude CLI is broken” just because an automated `claude --print ...` flow hangs. First check `which -a claude` and `claude doctor` to confirm install method (npm vs native) and which binary is actually being invoked.
- If Claude Code shows the toast about switching from npm → native installer, treat it as a *PATH / install-method mismatch* problem to verify, not an authentication problem to guess at.
- For headless/automation hangs, rerun with `--debug-file` and look specifically for OAuth token expiry/refresh failures; the likely fix path is re-auth (`claude auth logout` + `claude auth login`) or `claude setup-token`, not retrying for 15 minutes.

## 2026-03-02

- Don’t try to “fix” a user’s MCP setup when the real requirement is: headless automation must not load MCP at all. For Claude Code, solve this at the call-site with `--strict-mcp-config` plus an empty `--mcp-config` so evaluation runs fast and deterministically.

## 2026-03-11

- When a user says to ship an external skill bundle, do not assume only `SKILL.md` matters. Inspect and vendor the entire bundle from the source path they named, and preserve that original source path untouched unless they explicitly authorize changing it.
- When adding packaged-resource tests, don’t stop at asserting files exist in the live source tree. Add at least one automated built-artifact check so wheel/sdist regressions are caught, and don’t assume `importlib.resources.files()` always maps to a real filesystem path.
- When setup-time readiness checks call helpers that read config-backed command overrides, verify the preflight path is using the loaded existing config rather than a reduced write-only config payload like `cfg_out`.

## 2026-03-29

- When adding a new changelog workflow, do not assume evaluator-default behavior can diverge from existing `ctx`/export flows. If the product already supports config/env-driven evaluator defaults, every new changelog entrypoint must honor the same precedence unless the user explicitly asks for a different model.
- When a setting can come from per-repo config, do not resolve it from the invocation context before the target repo is known. In multi-repo sync flows, derive repo-scoped config only after each session resolves to its destination project.
