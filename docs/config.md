# Configuration

`ai-code-sessions` supports configuration at three levels: CLI flags, environment variables, and config files. This guide covers all the options and how they interact.

---

## Configuration Precedence

When the same setting is specified in multiple places, the following precedence applies (highest to lowest):

1. **CLI flags** — `--changelog`, `--changelog-actor`, etc.
2. **Environment variables** — `CTX_CHANGELOG`, `CTX_ACTOR`, etc.
3. **Per-repo config** — `.ai-code-sessions.toml` in project root
4. **Global config** — User-wide config file

---

## Setup Wizard

The easiest way to configure `ai-code-sessions` is the interactive wizard:

```bash
ais setup
```

The wizard will:

1. Ask which CLI(s) `ais ctx` should wrap
2. Ask whether changelog generation is enabled
3. Ask which evaluator should generate changelog entries
4. Check workflow readiness (`codex`, `claude`, `jq`, `rg`, and optional helpers)
5. Ask about global and repo config scope unless flags already fixed that choice
6. Optionally update your `.gitignore`
7. Print manual skill-install guidance for Codex and/or Claude
8. Write the requested config files

### Wizard Options

```bash
# Run full wizard
ais setup

# Only write global config
ais setup --no-repo

# Only write per-repo config
ais setup --no-global

# Overwrite existing configs without prompting
ais setup --force

# Target a specific repo
ais setup --project-root /path/to/my/repo
```

### Config Scope vs Skill Scope

These are separate choices:

- **Config scope** controls where `.ai-code-sessions.toml` values are written.
- **Skill-install scope** controls where the packaged changelog skill is copied.

Use `ais skill path changelog` plus the install steps in [`skills.md`](skills.md) to copy the shipped bundle into:

- `~/.codex/skills/changelog/`
- `./.codex/skills/changelog/`
- `~/.claude/skills/changelog/`
- `./.claude/skills/changelog/`

`skills.md` includes both POSIX-shell examples and Windows PowerShell guidance.

---

## Inspect Resolved Config

See the final values and where they came from:

```bash
ais config show
ais config show --json
```

---

## Config File Locations

### Global Config

User-wide defaults that apply to all projects.

| Platform | Location |
|----------|----------|
| macOS | `~/Library/Application Support/ai-code-sessions/config.toml` |
| Linux | `~/.config/ai-code-sessions/config.toml` |
| Windows | `%APPDATA%\ai-code-sessions\config.toml` |

> **Override:** Set `AI_CODE_SESSIONS_CONFIG` to use a custom global config path:
> ```bash
> export AI_CODE_SESSIONS_CONFIG="/custom/path/to/config.toml"
> ```

### Per-Repo Config

Project-specific settings that override global config. Place in your project root:

- `.ai-code-sessions.toml` (preferred)
- `.ais.toml` (alternate)

---

## Config File Format

Config files use TOML format with two sections:

### `[ctx]` Section

Settings for the `ais ctx` command:

```toml
[ctx]
tz = "America/Los_Angeles"    # Timezone for session folder names (IANA format)
codex_cmd = "codex"           # Optional override for Codex CLI executable
claude_cmd = "claude"         # Optional override for Claude CLI executable
```

### `[changelog]` Section

Settings for changelog generation:

```toml
[changelog]
enabled = true                 # Enable changelog generation by default
actor = "your-github-username" # Who gets credited in changelogs
evaluator = "codex"           # "codex" or "claude"
model = ""                     # Blank uses tool defaults (Claude defaults to `opus[1m]`)
claude_thinking_tokens = 8192  # Max thinking tokens (Claude only)
```

When `evaluator = "claude"` and `model` is blank, changelog evaluation uses `opus[1m]` by default.

### Complete Example

```toml
# .ai-code-sessions.toml

[ctx]
tz = "America/New_York"
codex_cmd = "codex"
claude_cmd = "claude"

[changelog]
enabled = true
actor = "jsmith"
evaluator = "claude"
model = ""  # Optional; blank uses Claude's long-context default (`opus[1m]`)
claude_thinking_tokens = 16384
```

---

## Environment Variables

Environment variables override config file settings. Useful for:
- Quick overrides in your shell
- CI/CD pipelines
- Per-session customization

### Session Settings

| Variable | Description | Default |
|----------|-------------|---------|
| `CTX_TZ` | Timezone for folder names | `America/Los_Angeles` |
| `CTX_CODEX_CMD` | Codex executable name/path | `codex` |
| `CTX_CLAUDE_CMD` | Claude executable name/path | `claude` |

### Changelog Settings

| Variable | Description | Default |
|----------|-------------|---------|
| `CTX_CHANGELOG` | Enable changelog (`1`, `true`) | disabled |
| `CTX_ACTOR` | Changelog actor (username) | auto-detected |
| `CTX_CHANGELOG_EVALUATOR` | `codex` or `claude` | `codex` |
| `CTX_CHANGELOG_MODEL` | Model for evaluator (must be supported by selected CLI) | tool default (`opus[1m]` for Claude) |
| `CTX_CHANGELOG_CLAUDE_THINKING_TOKENS` | Max thinking tokens | `8192` |

### Alternative Variable Names

Some settings have multiple variable names for compatibility:

| Setting | Variables (all equivalent) |
|---------|---------------------------|
| Changelog enabled | `CTX_CHANGELOG`, `AI_CODE_SESSIONS_CHANGELOG` |
| Changelog actor | `CTX_ACTOR`, `CHANGELOG_ACTOR` |

---

## Examples

### Minimal Setup

Just set your username and enable changelogs:

```bash
# In ~/.bashrc or ~/.zshrc
export CTX_ACTOR="myusername"
export CTX_CHANGELOG=1
```

### Full Environment Setup

All available settings:

```bash
# Session settings
export CTX_TZ="America/Los_Angeles"
export CTX_CODEX_CMD="codex"
export CTX_CLAUDE_CMD="claude"

# Changelog settings
export CTX_CHANGELOG=1
export CTX_ACTOR="myusername"
export CTX_CHANGELOG_EVALUATOR="codex"
export CTX_CHANGELOG_MODEL=""
```

### Per-Repo Config (Open Source Project)

For a public repo where you don't want to commit changelogs:

```toml
# .ai-code-sessions.toml
[ctx]
tz = "UTC"

[changelog]
enabled = true
actor = "maintainer-username"
```

And in `.gitignore`:

```gitignore
.changelog/
.codex/sessions/
.claude/sessions/
```

### Per-Repo Config (Private Project)

For a private repo where you want team visibility:

```toml
# .ai-code-sessions.toml
[ctx]
tz = "America/New_York"

[changelog]
enabled = true
actor = "team-bot"
evaluator = "claude"
model = ""  # Optional; blank uses the Claude default (`opus[1m]`)
```

### CI/CD Configuration

For automated changelog generation in CI:

```yaml
# .github/workflows/ai-changelog.yml
env:
  CTX_ACTOR: "ci-bot"
  CTX_CHANGELOG: "1"
  CTX_CHANGELOG_EVALUATOR: "codex"

steps:
  - name: Generate changelogs
    run: ais changelog backfill --project-root .
```

---

## Timezone Reference

The `tz` setting uses IANA timezone names. Common values:

| Region | Timezone |
|--------|----------|
| US Pacific | `America/Los_Angeles` |
| US Mountain | `America/Denver` |
| US Central | `America/Chicago` |
| US Eastern | `America/New_York` |
| UK | `Europe/London` |
| Central Europe | `Europe/Berlin` |
| Japan | `Asia/Tokyo` |
| UTC | `UTC` |

Find your timezone:

```bash
# macOS/Linux
timedatectl show --property=Timezone --value 2>/dev/null || \
  readlink /etc/localtime | sed 's|.*/zoneinfo/||'
```

---

## Debugging Configuration

### View Current Config

The setup wizard shows which files exist:

```bash
ais setup
# Output shows: "Repo config: /path/to/.ai-code-sessions.toml"
# Output shows: "Global config: /path/to/config.toml"
```

### Check Environment Variables

```bash
env | grep -E '^(CTX_|AI_CODE_SESSIONS_|CHANGELOG_)' | sort
```

### Test Changelog Generation

```bash
# Dry run to see if changelog would be generated
ais export-latest ... --changelog 2>&1 | head -20
```

---

## Migrating from Environment to Config

If you've been using environment variables and want to switch to config files:

1. Run `ais setup` to create initial configs
2. Remove environment variables from your shell config
3. Verify by running `env | grep CTX_` (should be empty)

The setup wizard will read your current environment variables as defaults, making the migration seamless.
