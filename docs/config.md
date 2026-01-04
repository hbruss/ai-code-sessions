# Configuration

`ai-code-sessions` supports **global** (per-user) and **per-repo** configuration using TOML.

Precedence (highest → lowest):

1. CLI flags
2. Environment variables
3. Per-repo config
4. Global config

## Setup wizard

Run the interactive wizard:

```bash
ais setup
```

It can write:

- Global config (user-wide defaults)
- Per-repo config inside the current git repo

## Global config location

The default global config path is OS-specific:

- macOS: `~/Library/Application Support/ai-code-sessions/config.toml`
- Linux: `~/.config/ai-code-sessions/config.toml` (or `$XDG_CONFIG_HOME/ai-code-sessions/config.toml`)
- Windows: `%APPDATA%\\ai-code-sessions\\config.toml`

Override with:

```bash
export AI_CODE_SESSIONS_CONFIG="/absolute/path/to/config.toml"
```

## Per-repo config location

In your project repo root:

- `.ai-code-sessions.toml` (preferred)
- `.ais.toml` (alternate)

## Example config

```toml
[ctx]
tz = "America/Los_Angeles"

[changelog]
enabled = true
actor = "your-github-username"
evaluator = "codex"        # or "claude"
model = ""                 # blank uses tool defaults
claude_thinking_tokens = 8192
```

## Environment variables (overrides)

Useful overrides:

- `CTX_TZ` — time zone for session folder naming
- `CTX_CODEX_CMD` / `CTX_CLAUDE_CMD` — override the CLI executable name/path
- `CTX_CHANGELOG` — enable changelog generation (`1`/`true`)
- `CTX_ACTOR` — changelog actor (recommended: GitHub username)
