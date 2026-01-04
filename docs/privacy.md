# Privacy & safety notes

These transcripts can include **anything the model saw**:

- Prompts and assistant replies
- Tool calls and full tool outputs (including diffs, file contents, stack traces)
- File paths, repo names, and other environment details
- Potentially secrets (tokens, credentials, customer data) if they were present in context or output

## Treat transcripts as sensitive by default

Practical guidance:

- Assume `index.html`, `page-*.html`, and the copied JSON/JSONL source file can contain secrets.
- Avoid sharing transcripts outside your machine unless you’ve reviewed/redacted them.
- Consider adding `.codex/`, `.claude/`, and `.changelog/` to `.gitignore` in repos where you use `ais ctx`, so artifacts don’t get committed accidentally.

## Gist publishing

`ai-code-sessions` supports `--gist` (inherited from the upstream tool). This uploads the generated HTML files via the GitHub CLI (`gh`).

Notes:

- By default, gists are created without `--public` (so they’re “secret/unlisted”, not truly private).
- Once uploaded, you should assume the transcript URL could be shared or leaked.
