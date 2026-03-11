# Skills

`ai-code-sessions` now ships the changelog skill bundle inside the installed package, but it does **not** install that skill into Codex or Claude automatically. Installation is manual by design so users can choose the right scope and inspect exactly what gets copied.

## What Ships

The packaged changelog bundle includes:

- `SKILL.md`
- `changelog_utils.py`
- `prime-session.sh`

Find the installed bundle path with:

```bash
ais skill path changelog
```

## Prerequisites

The packaged changelog skill helper scripts work best with:

- recommended: `jq`
- recommended: `rg`
- optional: `fd`

If `jq` or `rg` are missing, `ais setup` reports a warning because the helper scripts are limited, but `ais ctx` and changelog generation can still work.

The onboarding wizard (`ais setup`) checks these for you and reports `PASS`, `WARN`, or `FAIL`.

## Choose A Scope

Use **user-wide** installation when you want the same skill available in every repo on your machine.

Use **project-local** installation when you want the skill to travel with one repo, or when you want teammates to install it into a repo-scoped `.codex/` or `.claude/` directory.

## Codex Install Targets

### Windows Note

The examples below use POSIX shell commands. On Windows, use the PowerShell examples in the next section. Also note that `prime-session.sh` is a POSIX shell helper, so running that helper on Windows requires a POSIX shell environment.

### User-Wide Codex

```bash
mkdir -p ~/.codex/skills/changelog
cp -R "$(ais skill path changelog)"/. ~/.codex/skills/changelog/
test -f ~/.codex/skills/changelog/SKILL.md
```

### Project-Local Codex

Run from the target repo root:

```bash
mkdir -p ./.codex/skills/changelog
cp -R "$(ais skill path changelog)"/. ./.codex/skills/changelog/
test -f ./.codex/skills/changelog/SKILL.md
```

## Claude Install Targets

### User-Wide Claude

```bash
mkdir -p ~/.claude/skills/changelog
cp -R "$(ais skill path changelog)"/. ~/.claude/skills/changelog/
test -f ~/.claude/skills/changelog/SKILL.md
```

### Project-Local Claude

Run from the target repo root:

```bash
mkdir -p ./.claude/skills/changelog
cp -R "$(ais skill path changelog)"/. ./.claude/skills/changelog/
test -f ./.claude/skills/changelog/SKILL.md
```

## PowerShell Examples

### User-Wide Codex

```powershell
$bundle = ais skill path changelog
New-Item -ItemType Directory -Force -Path "$HOME/.codex/skills/changelog" | Out-Null
Copy-Item -Recurse -Force (Join-Path $bundle '*') "$HOME/.codex/skills/changelog"
Test-Path "$HOME/.codex/skills/changelog/SKILL.md"
```

### Project-Local Codex

Run from the target repo root:

```powershell
$bundle = ais skill path changelog
New-Item -ItemType Directory -Force -Path "./.codex/skills/changelog" | Out-Null
Copy-Item -Recurse -Force (Join-Path $bundle '*') "./.codex/skills/changelog"
Test-Path "./.codex/skills/changelog/SKILL.md"
```

### User-Wide Claude

```powershell
$bundle = ais skill path changelog
New-Item -ItemType Directory -Force -Path "$HOME/.claude/skills/changelog" | Out-Null
Copy-Item -Recurse -Force (Join-Path $bundle '*') "$HOME/.claude/skills/changelog"
Test-Path "$HOME/.claude/skills/changelog/SKILL.md"
```

### Project-Local Claude

Run from the target repo root:

```powershell
$bundle = ais skill path changelog
New-Item -ItemType Directory -Force -Path "./.claude/skills/changelog" | Out-Null
Copy-Item -Recurse -Force (Join-Path $bundle '*') "./.claude/skills/changelog"
Test-Path "./.claude/skills/changelog/SKILL.md"
```

## Verification

After copying the bundle:

```bash
ais skill path changelog
ls ~/.codex/skills/changelog 2>/dev/null
ls ~/.claude/skills/changelog 2>/dev/null
```

For repo-local installs, replace those paths with `./.codex/skills/changelog` or `./.claude/skills/changelog`.

## Relationship To `ais setup`

`ais setup` now helps with three things:

- choosing which CLI(s) `ais ctx` should wrap
- choosing which CLI should generate changelog entries
- printing exact manual skill-install commands for the relevant Codex and Claude targets

The wizard does not copy the files for you. It tells you exactly where the packaged bundle lives and exactly how to install it into the scope you want.
