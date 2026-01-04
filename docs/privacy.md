# Privacy & Safety

AI coding transcripts can contain sensitive information. This guide explains what gets captured and how to handle it safely.

---

## What Transcripts Contain

Transcripts include **everything the AI saw and produced**:

| Content Type | Examples |
|--------------|----------|
| Your prompts | "Fix the login bug in auth.py" |
| AI responses | Explanations, suggestions, code |
| Tool calls | Bash commands, file reads/writes |
| Tool outputs | Command output, file contents, diffs |
| File paths | `/home/user/myproject/src/secret.py` |
| Environment details | Repo names, hostnames, usernames |
| Potentially secrets | Tokens, API keys, credentials if exposed |

**The transcripts are a complete record of the session.** If a secret appeared in context or output, it's in the transcript.

---

## Default Behavior

When you run `ais ctx`:

1. **HTML transcripts** are written to your project directory (`.codex/sessions/` or `.claude/sessions/`)
2. **Source JSONL** is copied alongside the HTML
3. **Changelog entries** (if enabled) summarize the session

By default, none of this is:
- Uploaded anywhere
- Committed to git (unless you explicitly do so)
- Shared outside your machine

---

## Recommendations

### 1. Treat Transcripts as Sensitive

Assume every transcript file could contain secrets:

- `index.html`, `page-*.html`
- Copied JSONL files
- `source_match.json` (contains file paths)
- `.changelog/*/entries.jsonl` (contains file paths and summaries)

### 2. Add to `.gitignore`

Prevent accidental commits:

```gitignore
# AI session artifacts
.codex/sessions/
.claude/sessions/
.changelog/

# Or ignore everything in .codex and .claude
.codex/
.claude/
```

The `ais setup` wizard can add these entries for you.

### 3. Review Before Sharing

If you need to share a transcript:

1. Open the HTML and read through it
2. Look for:
   - API keys or tokens
   - Passwords or credentials
   - Customer data
   - Internal URLs or hostnames
   - Sensitive file paths
3. Redact or remove sensitive sections

### 4. Be Careful with Gist Publishing

The `--gist` flag (inherited from upstream) uploads transcripts to GitHub Gist:

```bash
# This uploads to GitHub!
ais json session.jsonl -o ./out --gist
```

Notes:
- Gists are created as "secret" (unlisted), not truly private
- Anyone with the URL can view them
- Once uploaded, consider the content potentially public

**Only use `--gist` after reviewing the transcript for sensitive data.**

---

## What Changelog Entries Contain

Changelog entries (`.changelog/*/entries.jsonl`) are summaries, not full transcripts. They include:

| Field | Privacy Concern |
|-------|-----------------|
| `summary` | May describe what you worked on |
| `bullets` | May describe specific changes |
| `files_created/modified/deleted` | Full file paths |
| `commits` | Git commit hashes (linkable) |
| `transcript_path` | Path to full transcript |
| `tags` | Classification only, low risk |

Changelog entries do **not** contain:
- Full file contents
- Command output
- Actual code

### Privacy Trade-off

Changelogs are designed to be low-noise summaries. They're useful for:
- Context in future AI sessions
- Team visibility
- Release notes

But they still reveal what you worked on and which files you touched.

---

## Sensitive Data in Prompts

If you accidentally paste a secret into a prompt:

1. The secret is now in the session log
2. It will appear in the transcript
3. Changelog may reference it in summaries

**Prevention is best:**
- Use environment variables for secrets
- Reference secret names, not values
- Use `.env` files that are gitignored

**If it happens:**
1. Rotate the exposed credential immediately
2. Delete the affected transcript files
3. Check `.changelog/*/entries.jsonl` for references

---

## Data Retention

### Your Machine

Session logs and transcripts persist indefinitely unless you delete them:

```bash
# Delete specific session
rm -rf .codex/sessions/2026-01-02-*

# Delete all sessions
rm -rf .codex/sessions/
rm -rf .claude/sessions/

# Delete changelog
rm -rf .changelog/
```

### AI Provider Logs

The native session logs (`~/.codex/sessions/` and `~/.claude/projects/`) are managed by those tools, not by `ai-code-sessions`. Check their documentation for retention policies.

---

## Team and Enterprise Considerations

### Shared Repositories

If multiple people work on a repo with `ai-code-sessions`:

1. Each person's transcripts go to `.codex/sessions/` in their local clone
2. If sessions aren't gitignored, they could be committed
3. Use `.gitignore` to prevent accidental sharing

### Changelog Attribution

Changelogs use the `actor` setting to attribute entries:

```toml
[changelog]
actor = "your-username"
```

Entries include the actor, so team members can see who generated each entry.

### Audit Trails

If you need an audit trail of AI-assisted work:
- Consider committing changelogs (after review)
- Use consistent `actor` values
- Transcripts provide full detail if needed

---

## Summary Checklist

- [ ] Add `.codex/sessions/`, `.claude/sessions/`, `.changelog/` to `.gitignore`
- [ ] Review transcripts before sharing
- [ ] Never use `--gist` without reviewing content
- [ ] Rotate any credentials that appeared in sessions
- [ ] Use environment variables for secrets, not inline values
