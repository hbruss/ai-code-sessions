# AI Lessons

## 2026-03-01

- Don’t assume “Claude CLI is broken” just because an automated `claude --print ...` flow hangs. First check `which -a claude` and `claude doctor` to confirm install method (npm vs native) and which binary is actually being invoked.
- If Claude Code shows the toast about switching from npm → native installer, treat it as a *PATH / install-method mismatch* problem to verify, not an authentication problem to guess at.
- For headless/automation hangs, rerun with `--debug-file` and look specifically for OAuth token expiry/refresh failures; the likely fix path is re-auth (`claude auth logout` + `claude auth login`) or `claude setup-token`, not retrying for 15 minutes.

## 2026-03-02

- Don’t try to “fix” a user’s MCP setup when the real requirement is: headless automation must not load MCP at all. For Claude Code, solve this at the call-site with `--strict-mcp-config` plus an empty `--mcp-config` so evaluation runs fast and deterministically.
