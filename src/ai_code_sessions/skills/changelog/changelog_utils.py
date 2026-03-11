"""
changelog_utils.py - Programmatic access to ai-code-sessions changelog

For interactive CLI use, prefer the CLI or ripgrep:
    ais changelog since yesterday
    ais changelog lint
    rg "topic" .changelog/

Use this module when you need:
- Integration with other Python scripts
- Complex filtering logic
- Building tools on top of the changelog
"""

from __future__ import annotations
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator


@dataclass
class ChangelogEntry:
    run_id: str
    created_at: str
    tool: str
    summary: str
    bullets: list[str]
    tags: list[str]
    touched_files: dict
    commits: list[dict]
    tests: list[dict]
    transcript: dict
    label: str | None = None
    continuation_of_run_id: str | None = None
    actor: str = ""
    project: str = ""
    project_root: str = ""
    start: str = ""
    end: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> ChangelogEntry:
        return cls(
            run_id=data.get("run_id", ""),
            created_at=data.get("created_at", ""),
            tool=data.get("tool", "unknown"),
            summary=data.get("summary", ""),
            bullets=data.get("bullets", []),
            tags=data.get("tags", []),
            touched_files=data.get("touched_files", {}),
            commits=data.get("commits", []),
            tests=data.get("tests", []),
            transcript=data.get("transcript", {}),
            label=data.get("label"),
            continuation_of_run_id=data.get("continuation_of_run_id"),
            actor=data.get("actor", ""),
            project=data.get("project", ""),
            project_root=data.get("project_root", ""),
            start=data.get("start", ""),
            end=data.get("end", ""),
        )


def iter_entries(repo_root: Path | str = ".") -> Iterator[ChangelogEntry]:
    """Iterate over all changelog entries, newest first."""
    repo_root = Path(repo_root)
    changelog_dir = repo_root / ".changelog"

    if not changelog_dir.exists():
        return

    entries = []
    for entries_file in changelog_dir.glob("*/entries.jsonl"):
        with open(entries_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

    entries.sort(key=lambda e: e.get("created_at", ""), reverse=True)
    for entry in entries:
        yield ChangelogEntry.from_dict(entry)


def recent(n: int = 5, repo_root: Path | str = ".") -> list[ChangelogEntry]:
    """Get the N most recent entries."""
    entries = []
    for entry in iter_entries(repo_root):
        entries.append(entry)
        if len(entries) >= n:
            break
    return entries


def search(query: str, repo_root: Path | str = ".") -> list[ChangelogEntry]:
    """Search entries by text in summary and bullets."""
    query = query.lower()
    results = []
    for entry in iter_entries(repo_root):
        if query in entry.summary.lower():
            results.append(entry)
        elif any(query in b.lower() for b in entry.bullets):
            results.append(entry)
    return results


def by_file(filepath: str, repo_root: Path | str = ".") -> list[ChangelogEntry]:
    """Find entries that touched a specific file."""
    results = []
    for entry in iter_entries(repo_root):
        tf = entry.touched_files
        all_files = (
            tf.get("created", [])
            + tf.get("modified", [])
            + tf.get("deleted", [])
            + [m.get("from", "") for m in tf.get("moved", [])]
            + [m.get("to", "") for m in tf.get("moved", [])]
        )
        if any(filepath in f for f in all_files):
            results.append(entry)
    return results


def by_tag(tag: str, repo_root: Path | str = ".") -> list[ChangelogEntry]:
    """Find entries with a specific tag."""
    tag = tag.lower()
    return [e for e in iter_entries(repo_root) if tag in [t.lower() for t in e.tags]]


def failing_tests(repo_root: Path | str = ".") -> list[ChangelogEntry]:
    """Find entries with failing tests."""
    return [e for e in iter_entries(repo_root) if any(t.get("result") == "fail" for t in e.tests)]


def _parse_relative_date(ref: str) -> datetime | None:
    """Parse relative date strings like '2 days ago', 'yesterday', 'last week'."""
    ref_lower = ref.lower().strip()
    now = datetime.now(timezone.utc)

    if ref_lower == "yesterday":
        return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    if ref_lower == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Pattern: "N days/weeks/hours ago"
    match = re.match(r"(\d+)\s+(day|week|hour|minute|month)s?\s+ago", ref_lower)
    if match:
        n = int(match.group(1))
        unit = match.group(2)
        if unit == "day":
            return now - timedelta(days=n)
        if unit == "week":
            return now - timedelta(weeks=n)
        if unit == "hour":
            return now - timedelta(hours=n)
        if unit == "minute":
            return now - timedelta(minutes=n)
        if unit == "month":
            return now - timedelta(days=n * 30)

    # Pattern: "last week/month"
    if ref_lower == "last week":
        return now - timedelta(weeks=1)
    if ref_lower == "last month":
        return now - timedelta(days=30)

    return None


def _parse_iso8601(s: str) -> datetime | None:
    """Parse an ISO8601 timestamp string."""
    if not s:
        return None
    try:
        # Handle various ISO formats
        s = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def since(ref: str, repo_root: Path | str = ".") -> list[ChangelogEntry]:
    """Get entries since a date or relative time.

    Args:
        ref: Date reference - ISO date (2026-01-06), relative ("yesterday", "3 days ago"),
             or git ref (abc1234, HEAD~5)
        repo_root: Repository root path

    Returns:
        List of entries since the given reference, newest first
    """
    repo_root = Path(repo_root)

    # Try ISO date first
    since_dt = _parse_iso8601(ref)

    # Try relative date
    if since_dt is None:
        since_dt = _parse_relative_date(ref)

    # Try git commit timestamp
    if since_dt is None:
        try:
            result = subprocess.run(
                ["git", "log", "-1", "--format=%cI", ref],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                since_dt = _parse_iso8601(result.stdout.strip())
        except Exception:
            pass

    if since_dt is None:
        raise ValueError(f"Could not parse '{ref}' as date or git ref")

    # Filter entries
    results = []
    for entry in iter_entries(repo_root):
        entry_dt = _parse_iso8601(entry.created_at) or _parse_iso8601(entry.start)
        if entry_dt and entry_dt >= since_dt:
            results.append(entry)

    return results


def looks_truncated(text: str) -> bool:
    """Check if text appears to be truncated mid-word or mid-sentence.

    Useful for detecting garbled changelog entries.
    """
    if not text:
        return False
    text = text.strip()
    if not text:
        return False

    # Valid sentence endings
    if re.search(r"[.!?:;)\]`\"']$", text):
        return False

    # Common abbreviations that end with lowercase
    if text.endswith(("etc", "ie", "eg", "vs", "al")):
        return False

    # Ends with lowercase letter (likely mid-word)
    if re.search(r"[a-z]$", text):
        return True

    # Ends with incomplete syntax
    if text.endswith(("/", "\\", "`", '"', "(", "[", "{", ",", "=")):
        return True

    return False


def validate_entry(entry: ChangelogEntry) -> tuple[bool, list[str]]:
    """Validate an entry for quality issues.

    Returns:
        Tuple of (is_valid, list of warning messages)
    """
    warnings = []

    if not entry.summary:
        warnings.append("Missing summary")
    elif looks_truncated(entry.summary):
        warnings.append(f"Summary may be truncated: ...{entry.summary[-30:]}")

    if not entry.bullets:
        warnings.append("Missing bullets")
    else:
        for i, bullet in enumerate(entry.bullets):
            if looks_truncated(bullet):
                warnings.append(f"Bullet {i} may be truncated: ...{bullet[-30:]}")
            if len(bullet.strip()) < 5:
                warnings.append(f"Bullet {i} suspiciously short")

    return len(warnings) == 0, warnings


def prime_context(n: int = 3, repo_root: Path | str = ".") -> str:
    """Generate a context summary string for session priming."""
    entries = recent(n, repo_root)
    if not entries:
        return "No changelog entries found."

    lines = []
    for entry in entries:
        lines.append(f"## {entry.label or 'Unlabeled'}")
        lines.append(f"**Summary:** {entry.summary}")
        if entry.tags:
            lines.append(f"**Tags:** {', '.join(entry.tags)}")
        for bullet in entry.bullets[:5]:
            lines.append(f"- {bullet}")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    repo = sys.argv[1] if len(sys.argv) > 1 else "."
    print(prime_context(3, repo))
