# Claude CLI: single native binary

- [x] Inspect current `claude` resolution (`which -a claude`)
- [x] Confirm which installs own each `claude` (nvm npm-global vs Homebrew npm-global)
- [x] Confirm native build location under `~/Library/Application Support/Claude/claude-code/*/claude`
- [x] Create a single stable `claude` entrypoint in `~/.local/bin/claude` pointing at the native build
- [x] Uninstall npm-global `@anthropic-ai/claude-code` from both prefixes to remove duplicate shims
- [ ] Resolve `claude --print` hang (likely OAuth refresh / long-lived token)
