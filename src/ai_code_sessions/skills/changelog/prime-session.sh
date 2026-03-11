#!/bin/bash
# prime-session.sh - Load context from ai-code-sessions changelog
#
# Usage: ./prime-session.sh [repo_root]
#
# Outputs a formatted summary of recent sessions for context priming.
# Use this for session startup. For ad-hoc searching during a session,
# use ripgrep directly: rg "topic" .changelog/

set -euo pipefail

REPO_ROOT="${1:-.}"
cd "$REPO_ROOT"

# Find changelog files (prefer fd if available, fall back to find)
if command -v fd &>/dev/null; then
    CHANGELOGS=$(fd -t f "entries.jsonl" .changelog 2>/dev/null || true)
else
    CHANGELOGS=$(find .changelog -name "entries.jsonl" 2>/dev/null || true)
fi

if [ -z "$CHANGELOGS" ]; then
    echo "No changelog found in $(pwd)"
    exit 0
fi

# Merge all changelogs and sort by date
ALL_ENTRIES=$(cat $CHANGELOGS 2>/dev/null | jq -s 'sort_by(.created_at) | reverse')
ENTRY_COUNT=$(echo "$ALL_ENTRIES" | jq 'length')

if [ "$ENTRY_COUNT" -eq 0 ]; then
    echo "Changelog exists but is empty."
    exit 0
fi

echo "# Session Context"
echo ""
echo "$ENTRY_COUNT sessions in changelog."
echo ""

# Recent sessions (last 5)
echo "## Recent Sessions"
echo ""

echo "$ALL_ENTRIES" | jq -r '
  .[:5] | .[] |
  "### \(.label // "Unlabeled")\n" +
  "**Date:** \(.start[:10]) | **Tool:** \(.tool)\n\n" +
  "**Summary:** \(.summary)\n\n" +
  (if .bullets then (.bullets | map("- " + .) | join("\n")) else "" end) + "\n\n" +
  (if .tags and (.tags | length) > 0 then "**Tags:** " + (.tags | join(", ")) + "\n" else "" end) +
  (if .commits and (.commits | length) > 0 then "**Commits:** " + ([.commits[].message][:3] | join("; ")) + "\n" else "" end) +
  "\n---\n"
'

# Recently touched files (compact)
echo "## Recent Files"
echo ""
echo "$ALL_ENTRIES" | jq -r '
  [.[:5] | .[].touched_files | (.created // [])[], (.modified // [])[]] | 
  unique | .[:15] | .[]
' 2>/dev/null | sort -u

# Quick checks using ripgrep (faster than jq for pattern matching)
echo ""

# Failing tests
if rg -q '"result":\s*"fail"' .changelog/*/entries.jsonl 2>/dev/null; then
    echo "## ⚠️ Failing Tests Detected"
    rg '"result":\s*"fail"' .changelog/*/entries.jsonl | head -3
    echo ""
fi

# In-progress work
if rg -qi 'wip|part [0-9]|incomplete' .changelog/*/entries.jsonl 2>/dev/null; then
    echo "## 🔄 Possible WIP"
    rg -i '"label".*wip|"label".*part [0-9]' .changelog/*/entries.jsonl 2>/dev/null | head -3 || true
    echo ""
fi

# Entry quality issues (if ais CLI is available)
if command -v ais &>/dev/null; then
    LINT_OUTPUT=$(ais changelog lint 2>&1 || true)
    if echo "$LINT_OUTPUT" | grep -q "Found issues"; then
        echo "## ⚠️ Changelog Quality Issues"
        echo "Some entries may have truncated or garbled content."
        echo "Run \`ais changelog lint\` to see details."
        echo ""
    fi
fi

echo "---"
echo "_Use \`rg \"topic\" .changelog/\` to search for specific topics_"
echo "_Use \`ais changelog since yesterday\` for recent entries_"
