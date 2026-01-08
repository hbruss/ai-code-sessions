"""Convert Codex CLI and Claude Code session logs to HTML transcripts."""

import json
import html
import hashlib
import itertools
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import click
import httpx
from jinja2 import Environment, PackageLoader
import markdown

# Set up Jinja2 environment
_jinja_env = Environment(
    loader=PackageLoader("ai_code_sessions", "templates"),
    autoescape=True,
)

# Load macros template and expose macros
_macros_template = _jinja_env.get_template("macros.html")
_macros = _macros_template.module

_JSONL_IO_LOCK = threading.Lock()
_LOG_IO_LOCK = threading.Lock()
_LOGGER = logging.getLogger("ai_code_sessions")


def get_template(name):
    """Get a Jinja2 template by name."""
    return _jinja_env.get_template(name)


def configure_logging(*, verbosity: int, log_file: Path | None = None) -> None:
    """Configure structured logging for CLI usage."""
    level = logging.WARNING
    if verbosity == 1:
        level = logging.INFO
    elif verbosity >= 2:
        level = logging.DEBUG

    handlers: list[logging.Handler] = []
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(level)
    handlers.append(stream_handler)

    if log_file is not None:
        log_path = Path(log_file).expanduser()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setLevel(level)
        handlers.append(file_handler)

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


# Regex to match git commit output: [branch hash] message
COMMIT_PATTERN = re.compile(r"\[[\w\-/]+ ([a-f0-9]{7,})\] (.+?)(?:\n|$)")

# Regex to detect GitHub repo from git push output (e.g., github.com/owner/repo/pull/new/branch)
GITHUB_REPO_PATTERN = re.compile(r"github\.com/([a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+)/pull/new/")

# Regex to detect GitHub repo from a git remote URL (SSH/HTTPS).
GITHUB_REPO_URL_PATTERN = re.compile(r"github\.com[:/](?P<repo>[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+)(?:\.git)?(?:$|[/?#])")

PROMPTS_PER_PAGE = 5
LONG_TEXT_THRESHOLD = 300  # Characters - text blocks longer than this are shown in index
SEARCH_INDEX_TEXT_MAX_CHARS = 2000
SEARCH_INDEX_SCHEMA_VERSION = 1
_OUTPUT_PRUNE_GLOBS = ("index.html", "page-*.html", "search_index.json")


def _guard_output_dir_for_clean(output_dir: Path, project_root: Path | None = None) -> None:
    resolved = output_dir.resolve()
    dangerous = {Path("/").resolve(), Path.home().resolve()}
    if project_root is not None:
        dangerous.add(project_root.resolve())
    if resolved in dangerous or len(resolved.parts) < 3:
        raise click.ClickException(f"Refusing to clean dangerous output directory: {resolved}")


def prepare_output_dir(*, output_dir: Path, mode: str, project_root: Path | None = None) -> None:
    mode = (mode or "merge").lower()
    if mode not in ("merge", "overwrite", "clean"):
        raise click.ClickException(f"Invalid output mode: {mode!r}")
    if mode == "clean":
        _guard_output_dir_for_clean(output_dir, project_root)
        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    if mode == "overwrite":
        for pattern in _OUTPUT_PRUNE_GLOBS:
            for path in output_dir.glob(pattern):
                if path.is_file():
                    path.unlink()


def prune_stale_pages(*, output_dir: Path, total_pages: int) -> None:
    if total_pages <= 0:
        return
    pattern = re.compile(r"page-(\d+)\\.html$")
    for path in output_dir.glob("page-*.html"):
        match = pattern.match(path.name)
        if not match:
            continue
        try:
            page_num = int(match.group(1))
        except ValueError:
            continue
        if page_num > total_pages:
            path.unlink()


def extract_text_from_content(content):
    """Extract plain text from message content.

    Handles both string content (older format) and array content (newer format).

    Args:
        content: Either a string or a list of content blocks like
                 [{"type": "text", "text": "..."}, {"type": "image", ...}]

    Returns:
        The extracted text as a string, or empty string if no text found.
    """
    if isinstance(content, str):
        return content.strip()
    elif isinstance(content, list):
        # Extract text from content blocks of type "text"
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if text:
                    texts.append(text)
        return " ".join(texts).strip()
    return ""


# Module-level variable for GitHub repo (set by generate_html)
_github_repo = None

# API constants
API_BASE_URL = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"


def get_session_summary(filepath, max_length=200):
    """Extract a human-readable summary from a session file.

    Supports both JSON and JSONL formats.
    Returns a summary string or "(no summary)" if none found.
    """
    filepath = Path(filepath)
    try:
        if filepath.suffix == ".jsonl":
            return _get_jsonl_summary(filepath, max_length)
        else:
            # For JSON files, try to get first user message
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            loglines = data.get("loglines", [])
            for entry in loglines:
                if entry.get("type") == "user":
                    msg = entry.get("message", {})
                    content = msg.get("content", "")
                    text = extract_text_from_content(content)
                    if text:
                        if len(text) > max_length:
                            return text[: max_length - 3] + "..."
                        return text
            return "(no summary)"
    except Exception:
        return "(no summary)"


def _get_jsonl_summary(filepath, max_length=200):
    """Extract summary from JSONL file."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    # First priority: summary type entries
                    if obj.get("type") == "summary" and obj.get("summary"):
                        summary = obj["summary"]
                        if len(summary) > max_length:
                            return summary[: max_length - 3] + "..."
                        return summary
                except json.JSONDecodeError:
                    continue

        # Second pass: find first non-meta user message
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if obj.get("type") == "user" and not obj.get("isMeta") and obj.get("message", {}).get("content"):
                        content = obj["message"]["content"]
                        text = extract_text_from_content(content)
                        if text and not text.startswith("<"):
                            if len(text) > max_length:
                                return text[: max_length - 3] + "..."
                            return text
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass

    return "(no summary)"


def find_local_sessions(folder, limit=10):
    """Find recent JSONL session files in the given folder.

    Returns a list of (Path, summary) tuples sorted by modification time.
    Excludes agent files and warmup/empty sessions.
    """
    folder = Path(folder)
    if not folder.exists():
        return []

    results = []
    for f in folder.glob("**/*.jsonl"):
        if f.name.startswith("agent-"):
            continue
        summary = get_session_summary(f)
        # Skip boring/empty sessions
        if summary.lower() == "warmup" or summary == "(no summary)":
            continue
        results.append((f, summary))

    # Sort by modification time, most recent first
    results.sort(key=lambda x: x[0].stat().st_mtime, reverse=True)
    return results[:limit]


def get_project_display_name(folder_name):
    """Convert encoded folder name to readable project name.

    Claude Code stores projects in folders like:
    - -home-user-projects-myproject -> myproject
    - -mnt-c-Users-name-Projects-app -> app

    For nested paths under common roots (home, projects, code, Users, etc.),
    extracts the meaningful project portion.
    """
    # Common path prefixes to strip
    prefixes_to_strip = [
        "-home-",
        "-mnt-c-Users-",
        "-mnt-c-users-",
        "-Users-",
    ]

    name = folder_name
    for prefix in prefixes_to_strip:
        if name.lower().startswith(prefix.lower()):
            name = name[len(prefix) :]
            break

    # Split on dashes and find meaningful parts
    parts = name.split("-")

    # Common intermediate directories to skip
    skip_dirs = {"projects", "code", "repos", "src", "dev", "work", "documents"}

    # Find the first meaningful part (after skipping username and common dirs)
    meaningful_parts = []
    found_project = False

    for i, part in enumerate(parts):
        if not part:
            continue
        # Skip the first part if it looks like a username (before common dirs)
        if i == 0 and not found_project:
            # Check if next parts contain common dirs
            remaining = [p.lower() for p in parts[i + 1 :]]
            if any(d in remaining for d in skip_dirs):
                continue
        if part.lower() in skip_dirs:
            found_project = True
            continue
        meaningful_parts.append(part)
        found_project = True

    if meaningful_parts:
        return "-".join(meaningful_parts)

    # Fallback: return last non-empty part or original
    for part in reversed(parts):
        if part:
            return part
    return folder_name


def find_all_sessions(folder, include_agents=False):
    """Find all sessions in a Claude projects folder, grouped by project.

    Returns a list of project dicts, each containing:
    - name: display name for the project
    - path: Path to the project folder
    - sessions: list of session dicts with path, summary, mtime, size

    Sessions are sorted by modification time (most recent first) within each project.
    Projects are sorted by their most recent session.
    """
    folder = Path(folder)
    if not folder.exists():
        return []

    projects = {}

    for session_file in folder.glob("**/*.jsonl"):
        # Skip agent files unless requested
        if not include_agents and session_file.name.startswith("agent-"):
            continue

        # Get summary and skip boring sessions
        summary = get_session_summary(session_file)
        if summary.lower() == "warmup" or summary == "(no summary)":
            continue

        # Get project folder
        project_folder = session_file.parent
        project_key = project_folder.name

        if project_key not in projects:
            projects[project_key] = {
                "name": get_project_display_name(project_key),
                "path": project_folder,
                "sessions": [],
            }

        stat = session_file.stat()
        projects[project_key]["sessions"].append(
            {
                "path": session_file,
                "summary": summary,
                "mtime": stat.st_mtime,
                "size": stat.st_size,
            }
        )

    # Sort sessions within each project by mtime (most recent first)
    for project in projects.values():
        project["sessions"].sort(key=lambda s: s["mtime"], reverse=True)

    # Convert to list and sort projects by most recent session
    result = list(projects.values())
    result.sort(key=lambda p: p["sessions"][0]["mtime"] if p["sessions"] else 0, reverse=True)

    return result


def generate_batch_html(source_folder, output_dir, include_agents=False, progress_callback=None):
    """Generate HTML archive for all sessions in a Claude projects folder.

    Creates:
    - Master index.html listing all projects
    - Per-project directories with index.html listing sessions
    - Per-session directories with transcript pages

    Args:
        source_folder: Path to the Claude projects folder
        output_dir: Path for output archive
        include_agents: Whether to include agent-* session files
        progress_callback: Optional callback(project_name, session_name, current, total)
            called after each session is processed

    Returns statistics dict with total_projects, total_sessions, failed_sessions, output_dir.
    """
    source_folder = Path(source_folder)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Find all sessions
    projects = find_all_sessions(source_folder, include_agents=include_agents)

    # Calculate total for progress tracking
    total_session_count = sum(len(p["sessions"]) for p in projects)
    processed_count = 0
    successful_sessions = 0
    failed_sessions = []

    module_override = sys.modules.get("ai_code_sessions")
    generate_fn = getattr(module_override, "generate_html", generate_html) if module_override else generate_html

    # Process each project
    for project in projects:
        project_dir = output_dir / project["name"]
        project_dir.mkdir(exist_ok=True)

        # Process each session
        for session in project["sessions"]:
            session_name = session["path"].stem
            session_dir = project_dir / session_name

            # Generate transcript HTML with error handling
            try:
                generate_fn(session["path"], session_dir)
                successful_sessions += 1
            except Exception as e:
                failed_sessions.append(
                    {
                        "project": project["name"],
                        "session": session_name,
                        "error": str(e),
                    }
                )

            processed_count += 1

            # Call progress callback if provided
            if progress_callback:
                progress_callback(project["name"], session_name, processed_count, total_session_count)

        # Generate project index
        _generate_project_index(project, project_dir)

    # Generate master index
    _generate_master_index(projects, output_dir)

    return {
        "total_projects": len(projects),
        "total_sessions": successful_sessions,
        "failed_sessions": failed_sessions,
        "output_dir": output_dir,
    }


def _generate_project_index(project, output_dir):
    """Generate index.html for a single project."""
    template = get_template("project_index.html")

    # Format sessions for template
    sessions_data = []
    for session in project["sessions"]:
        mod_time = datetime.fromtimestamp(session["mtime"])
        sessions_data.append(
            {
                "name": session["path"].stem,
                "summary": session["summary"],
                "date": mod_time.strftime("%Y-%m-%d %H:%M"),
                "size_kb": session["size"] / 1024,
            }
        )

    html_content = template.render(
        project_name=project["name"],
        sessions=sessions_data,
        session_count=len(sessions_data),
        css=CSS,
        js=JS,
    )

    output_path = output_dir / "index.html"
    output_path.write_text(html_content, encoding="utf-8")


def _generate_master_index(projects, output_dir):
    """Generate master index.html listing all projects."""
    template = get_template("master_index.html")

    # Format projects for template
    projects_data = []
    total_sessions = 0

    for project in projects:
        session_count = len(project["sessions"])
        total_sessions += session_count

        # Get most recent session date
        if project["sessions"]:
            most_recent = datetime.fromtimestamp(project["sessions"][0]["mtime"])
            recent_date = most_recent.strftime("%Y-%m-%d")
        else:
            recent_date = "N/A"

        projects_data.append(
            {
                "name": project["name"],
                "session_count": session_count,
                "recent_date": recent_date,
            }
        )

    html_content = template.render(
        projects=projects_data,
        total_projects=len(projects),
        total_sessions=total_sessions,
        css=CSS,
        js=JS,
    )

    output_path = output_dir / "index.html"
    output_path.write_text(html_content, encoding="utf-8")


def _peek_first_jsonl_object(filepath: Path):
    """Return the first JSON object from a JSONL file, or None."""
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def _looks_like_codex_rollout_jsonl(first_obj: dict) -> bool:
    if not isinstance(first_obj, dict):
        return False
    return (
        "payload" in first_obj
        and "timestamp" in first_obj
        and first_obj.get("type") in {"session_meta", "response_item", "event_msg"}
    )


def parse_session_file(filepath):
    """Parse a session file and return normalized data.

    Supports JSON and JSONL formats from:
    - Claude Code (local JSONL or exported JSON)
    - Codex CLI rollouts (JSONL)

    Returns a dict with 'loglines' key containing normalized entries.
    """
    filepath = Path(filepath)

    if filepath.suffix == ".jsonl":
        first = _peek_first_jsonl_object(filepath)
        if first and _looks_like_codex_rollout_jsonl(first):
            return _parse_codex_rollout_jsonl(filepath)
        return _parse_claude_jsonl_file(filepath)

    # Standard JSON format (Claude web JSON export)
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def _parse_claude_jsonl_file(filepath: Path):
    """Parse Claude Code JSONL file and convert to standard format."""
    loglines = []

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                entry_type = obj.get("type")

                # Skip non-message entries
                if entry_type not in ("user", "assistant"):
                    continue

                # Convert to standard format
                entry = {
                    "type": entry_type,
                    "timestamp": obj.get("timestamp", ""),
                    "message": obj.get("message", {}),
                }

                # Preserve isCompactSummary if present
                if obj.get("isCompactSummary"):
                    entry["isCompactSummary"] = True

                loglines.append(entry)
            except json.JSONDecodeError:
                continue

    return {"loglines": loglines, "source_format": "claude_jsonl"}


def _safe_json_loads(value):
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _infer_is_error_from_exec_output(output_text: str) -> bool:
    if not isinstance(output_text, str):
        return False
    m = re.search(r"Process exited with code (\\d+)", output_text)
    if not m:
        return False
    try:
        return int(m.group(1)) != 0
    except ValueError:
        return False


def _infer_exit_code_from_exec_output(output_text: str) -> int | None:
    if not isinstance(output_text, str):
        return None
    m = re.search(r"Process exited with code (\\d+)", output_text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _codex_convert_message_content(content):
    """Convert Codex message content blocks to Claude-like content blocks."""
    blocks = []
    if isinstance(content, str):
        if content:
            blocks.append({"type": "text", "text": content})
        return blocks
    if not isinstance(content, list):
        return blocks
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type in ("input_text", "output_text"):
            text = block.get("text", "")
            if text:
                blocks.append({"type": "text", "text": text})
        elif block_type == "output_image":
            # Preserve image blocks if present (Codex may store these in OpenAI-style)
            # Expected shape: {type: "output_image", image_url: {url: "data:..."}} etc.
            # Fall back to JSON rendering if we can't map cleanly.
            blocks.append({"type": "text", "text": json.dumps(block, ensure_ascii=False)})
        else:
            blocks.append({"type": "text", "text": json.dumps(block, ensure_ascii=False)})
    return blocks


def _parse_codex_rollout_jsonl(filepath: Path):
    """Parse Codex CLI rollout JSONL and convert to standard format.

    The Codex rollout format consists of lines with:
    {timestamp, type, payload}
    """
    loglines = []
    meta = {}

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            outer_type = obj.get("type")
            ts = obj.get("timestamp", "")
            payload = obj.get("payload", {}) if isinstance(obj.get("payload"), dict) else {}

            if outer_type == "session_meta":
                meta = {
                    "session_id": payload.get("id"),
                    "timestamp": payload.get("timestamp"),
                    "cwd": payload.get("cwd"),
                    "originator": payload.get("originator"),
                    "cli_version": payload.get("cli_version"),
                }
                git_payload = payload.get("git") if isinstance(payload.get("git"), dict) else {}
                git_meta: dict[str, str] = {}
                for key in ("commit_hash", "branch", "repository_url"):
                    value = git_payload.get(key)
                    if isinstance(value, str) and value.strip():
                        git_meta[key] = value.strip()
                if git_meta:
                    meta["git"] = git_meta
                continue

            if outer_type != "response_item":
                continue

            item_type = payload.get("type")
            if item_type == "message":
                role = payload.get("role")
                if role not in ("user", "assistant"):
                    continue
                content = payload.get("content", [])
                message = {"role": role, "content": _codex_convert_message_content(content)}
                loglines.append({"type": role, "timestamp": ts, "message": message})
                continue

            if item_type == "function_call":
                tool_name = payload.get("name", "Unknown tool")
                call_id = payload.get("call_id", "")
                args = _safe_json_loads(payload.get("arguments", ""))
                tool_input = args if isinstance(args, dict) else {"arguments": args}
                block = {"type": "tool_use", "name": tool_name, "input": tool_input, "id": call_id}
                message = {"role": "assistant", "content": [block]}
                loglines.append({"type": "assistant", "timestamp": ts, "message": message})
                continue

            if item_type == "custom_tool_call":
                tool_name = payload.get("name", "Unknown tool")
                call_id = payload.get("call_id", "")
                status = payload.get("status")
                input_value = payload.get("input")
                tool_input = {}
                if status:
                    tool_input["status"] = status
                if isinstance(input_value, dict):
                    tool_input.update(input_value)
                elif input_value is not None:
                    tool_input["input"] = input_value
                block = {"type": "tool_use", "name": tool_name, "input": tool_input, "id": call_id}
                message = {"role": "assistant", "content": [block]}
                loglines.append({"type": "assistant", "timestamp": ts, "message": message})
                continue

            if item_type == "function_call_output":
                output = payload.get("output", "")
                call_id = payload.get("call_id", "")
                is_error = _infer_is_error_from_exec_output(output)
                block = {
                    "type": "tool_result",
                    "tool_use_id": call_id or None,
                    "content": output,
                    "is_error": is_error,
                }
                message = {"role": "assistant", "content": [block]}
                loglines.append({"type": "assistant", "timestamp": ts, "message": message})
                continue

            if item_type == "reasoning":
                summary = payload.get("summary", [])
                parts = []
                if isinstance(summary, list):
                    for s in summary:
                        if isinstance(s, dict) and s.get("type") == "summary_text":
                            txt = s.get("text")
                            if txt:
                                parts.append(txt)
                thinking_text = "\n\n".join(parts).strip()
                if thinking_text:
                    block = {"type": "thinking", "thinking": thinking_text}
                    message = {"role": "assistant", "content": [block]}
                    loglines.append({"type": "assistant", "timestamp": ts, "message": message})
                continue

    return {"loglines": loglines, "meta": meta, "source_format": "codex_rollout"}


def _parse_iso8601(value: str):
    if not value or not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


CHANGELOG_ENTRY_SCHEMA_VERSION = 1

_CHANGELOG_ENTRY_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version",
        "run_id",
        "created_at",
        "tool",
        "actor",
        "project",
        "project_root",
        "label",
        "start",
        "end",
        "session_dir",
        "continuation_of_run_id",
        "transcript",
        "summary",
        "bullets",
        "tags",
        "touched_files",
        "tests",
        "commits",
    ],
    "properties": {
        "schema_version": {"type": "integer", "const": CHANGELOG_ENTRY_SCHEMA_VERSION},
        "run_id": {"type": "string", "minLength": 1},
        "created_at": {"type": "string", "minLength": 1},
        "tool": {"type": "string", "enum": ["codex", "claude", "unknown"]},
        "actor": {"type": "string", "minLength": 1},
        "project": {"type": "string", "minLength": 1},
        "project_root": {"type": "string", "minLength": 1},
        "label": {"type": ["string", "null"]},
        "start": {"type": "string", "minLength": 1},
        "end": {"type": "string", "minLength": 1},
        "session_dir": {"type": "string", "minLength": 1},
        "continuation_of_run_id": {"type": ["string", "null"]},
        "transcript": {
            "type": "object",
            "additionalProperties": False,
            "required": ["output_dir", "index_html", "source_jsonl", "source_match_json"],
            "properties": {
                "output_dir": {"type": "string", "minLength": 1},
                "index_html": {"type": "string", "minLength": 1},
                "source_jsonl": {"type": "string", "minLength": 1},
                "source_match_json": {"type": "string", "minLength": 1},
            },
        },
        "summary": {"type": "string", "minLength": 1, "maxLength": 500},
        "bullets": {
            "type": "array",
            "minItems": 1,
            "maxItems": 12,
            "items": {"type": "string", "minLength": 1, "maxLength": 240},
        },
        "tags": {
            "type": "array",
            "minItems": 0,
            "maxItems": 24,
            "items": {"type": "string", "minLength": 1, "maxLength": 64},
        },
        "touched_files": {
            "type": "object",
            "additionalProperties": False,
            "required": ["created", "modified", "deleted", "moved"],
            "properties": {
                "created": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                },
                "modified": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                },
                "deleted": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                },
                "moved": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["from", "to"],
                        "properties": {
                            "from": {"type": "string", "minLength": 1},
                            "to": {"type": "string", "minLength": 1},
                        },
                    },
                },
            },
        },
        "tests": {
            "type": "array",
            "minItems": 0,
            "maxItems": 50,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["cmd", "result"],
                "properties": {
                    "cmd": {"type": "string", "minLength": 1, "maxLength": 500},
                    "result": {"type": "string", "enum": ["pass", "fail", "unknown"]},
                },
            },
        },
        "commits": {
            "type": "array",
            "minItems": 0,
            "maxItems": 50,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["hash", "message"],
                "properties": {
                    "hash": {"type": "string", "minLength": 4, "maxLength": 64},
                    "message": {"type": "string", "minLength": 1, "maxLength": 300},
                },
            },
        },
        "notes": {"type": ["string", "null"], "maxLength": 800},
    },
}

_CHANGELOG_CODEX_OUTPUT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "additionalProperties": False,
    # Codex structured outputs require `required` to include every key in
    # `properties`. Optional fields should still be present (use null).
    "required": ["summary", "bullets", "tags", "notes"],
    "properties": {
        "summary": {"type": "string", "minLength": 1, "maxLength": 500},
        "bullets": {
            "type": "array",
            "minItems": 1,
            "maxItems": 12,
            "items": {"type": "string", "minLength": 1, "maxLength": 240},
        },
        "tags": {
            "type": "array",
            "minItems": 0,
            "maxItems": 24,
            "items": {"type": "string", "minLength": 1, "maxLength": 64},
        },
        "notes": {"type": ["string", "null"], "maxLength": 800},
    },
}


def _now_iso8601() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json_schema_tempfile(schema: dict) -> Path:
    tmp = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".schema.json", delete=False)
    try:
        tmp.write(json.dumps(schema, indent=2, ensure_ascii=False))
        tmp.flush()
        return Path(tmp.name)
    finally:
        tmp.close()


def _compute_run_id(*, tool: str, start: str, end: str, session_dir: Path, source_jsonl: Path) -> str:
    payload = {
        "tool": tool or "unknown",
        "start": start or "",
        "end": end or "",
        "session_dir": str(session_dir),
        "source_jsonl": str(source_jsonl),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return digest[:16]


def _append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, ensure_ascii=False) + "\n"
    with _JSONL_IO_LOCK:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)


def _load_existing_run_ids(entries_path: Path) -> set[str]:
    run_ids: set[str] = set()
    if not entries_path.exists():
        return run_ids
    try:
        with _JSONL_IO_LOCK:
            with open(entries_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    run_id = obj.get("run_id")
                    if isinstance(run_id, str) and run_id:
                        run_ids.add(run_id)
    except OSError:
        return run_ids
    return run_ids


def _detect_actor(*, project_root: Path) -> str:
    for key in (
        "CHANGELOG_ACTOR",
        "CTX_ACTOR",
        "GIT_AUTHOR_EMAIL",
        "GIT_AUTHOR_NAME",
        "USER",
    ):
        val = os.environ.get(key)
        if val:
            return val.strip()

    # Fall back to git config values if available.
    try:
        email = subprocess.check_output(
            ["git", "config", "--get", "user.email"],
            cwd=str(project_root),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if email:
            return email
    except Exception:
        pass

    try:
        name = subprocess.check_output(
            ["git", "config", "--get", "user.name"],
            cwd=str(project_root),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if name:
            return name
    except Exception:
        pass

    return "unknown"


def _env_truthy(name: str) -> bool:
    val = os.environ.get(name)
    if val is None:
        return False
    return val.strip().lower() in ("1", "true", "yes", "y", "on")


def _env_first(*names: str) -> str | None:
    for name in names:
        if not name:
            continue
        val = os.environ.get(name)
        if val is None:
            continue
        val = val.strip()
        if val:
            return val
    return None


_REPO_CONFIG_FILENAMES = (".ai-code-sessions.toml", ".ais.toml")


def _global_config_path() -> Path:
    override = os.environ.get("AI_CODE_SESSIONS_CONFIG")
    if override:
        return Path(override).expanduser()

    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "ai-code-sessions" / "config.toml"
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / "ai-code-sessions" / "config.toml"
        return home / "AppData" / "Roaming" / "ai-code-sessions" / "config.toml"

    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "ai-code-sessions" / "config.toml"
    return home / ".config" / "ai-code-sessions" / "config.toml"


def _repo_config_path(project_root: Path) -> Path:
    for name in _REPO_CONFIG_FILENAMES:
        candidate = project_root / name
        if candidate.exists():
            return candidate
    return project_root / _REPO_CONFIG_FILENAMES[0]


def _read_toml_file(path: Path) -> dict:
    try:
        raw = path.read_bytes()
    except OSError:
        return {}
    try:
        obj = tomllib.loads(raw.decode("utf-8"))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _deep_merge_dicts(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge_dicts(out[k], v)  # type: ignore[arg-type]
        else:
            out[k] = v
    return out


def _load_config(*, project_root: Path | None) -> dict:
    cfg: dict = {}

    global_path = _global_config_path()
    if global_path.exists():
        cfg = _deep_merge_dicts(cfg, _read_toml_file(global_path))

    if project_root is not None:
        repo_path = _repo_config_path(project_root)
        if repo_path.exists():
            cfg = _deep_merge_dicts(cfg, _read_toml_file(repo_path))

    return cfg


def _config_get(cfg: dict, dotted_key: str, default=None):
    cur = cfg
    for part in dotted_key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _config_has(cfg: dict, dotted_key: str) -> bool:
    cur = cfg
    for part in dotted_key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return True


def resolve_config_with_provenance(*, project_root: Path) -> tuple[dict, dict]:
    global_path = _global_config_path()
    repo_path = _repo_config_path(project_root)

    global_cfg = _read_toml_file(global_path) if global_path.exists() else {}
    repo_cfg = _read_toml_file(repo_path) if repo_path.exists() else {}

    resolved: dict = {"ctx": {}, "changelog": {}}
    provenance: dict = {}

    def set_value(key: str, value, source: str) -> None:
        section, field = key.split(".", 1)
        resolved.setdefault(section, {})[field] = value
        provenance[key] = source

    def from_env_or_cfg(key: str, env_vars: list[str], default):
        for env_var in env_vars:
            if env_var in os.environ:
                val = os.environ.get(env_var, "")
                return val, f"env:{env_var}"
        if _config_has(repo_cfg, key):
            return _config_get(repo_cfg, key), f"repo:{repo_path}"
        if _config_has(global_cfg, key):
            return _config_get(global_cfg, key), f"global:{global_path}"
        return default, "default"

    ctx_tz, src = from_env_or_cfg("ctx.tz", ["CTX_TZ"], "America/Los_Angeles")
    set_value("ctx.tz", ctx_tz, src)

    codex_cmd, src = from_env_or_cfg("ctx.codex_cmd", ["CTX_CODEX_CMD"], None)
    set_value("ctx.codex_cmd", codex_cmd, src)

    claude_cmd, src = from_env_or_cfg("ctx.claude_cmd", ["CTX_CLAUDE_CMD"], None)
    set_value("ctx.claude_cmd", claude_cmd, src)

    changelog_env = None
    changelog_src = None
    for env_var in ("AI_CODE_SESSIONS_CHANGELOG", "CTX_CHANGELOG"):
        if env_var in os.environ:
            changelog_env = _env_truthy(env_var)
            changelog_src = f"env:{env_var}"
            break
    if changelog_src:
        set_value("changelog.enabled", changelog_env, changelog_src)
    elif _config_has(repo_cfg, "changelog.enabled"):
        set_value("changelog.enabled", _config_get(repo_cfg, "changelog.enabled"), f"repo:{repo_path}")
    elif _config_has(global_cfg, "changelog.enabled"):
        set_value("changelog.enabled", _config_get(global_cfg, "changelog.enabled"), f"global:{global_path}")
    else:
        set_value("changelog.enabled", False, "default")

    actor, src = from_env_or_cfg("changelog.actor", ["CTX_ACTOR"], None)
    if actor in (None, ""):
        actor = _detect_actor(project_root=project_root)
        src = "auto:detect_actor"
    set_value("changelog.actor", actor, src)

    evaluator, src = from_env_or_cfg(
        "changelog.evaluator",
        ["CTX_CHANGELOG_EVALUATOR", "AI_CODE_SESSIONS_CHANGELOG_EVALUATOR"],
        None,
    )
    set_value("changelog.evaluator", evaluator, src)

    model, src = from_env_or_cfg(
        "changelog.model",
        ["CTX_CHANGELOG_MODEL", "AI_CODE_SESSIONS_CHANGELOG_MODEL"],
        None,
    )
    set_value("changelog.model", model, src)

    tokens, src = from_env_or_cfg(
        "changelog.claude_thinking_tokens",
        ["CTX_CHANGELOG_CLAUDE_THINKING_TOKENS", "AI_CODE_SESSIONS_CHANGELOG_CLAUDE_THINKING_TOKENS"],
        None,
    )
    set_value("changelog.claude_thinking_tokens", tokens, src)

    return resolved, provenance


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _render_config_toml(cfg: dict) -> str:
    lines: list[str] = []

    ctx_cfg = cfg.get("ctx") if isinstance(cfg.get("ctx"), dict) else {}
    if isinstance(ctx_cfg, dict) and ctx_cfg:
        lines.append("[ctx]")
        for key in ("tz", "codex_cmd", "claude_cmd"):
            val = ctx_cfg.get(key)
            if isinstance(val, str) and val.strip():
                lines.append(f"{key} = {_toml_string(val.strip())}")
        if lines and lines[-1] != "":
            lines.append("")

    changelog_cfg = cfg.get("changelog") if isinstance(cfg.get("changelog"), dict) else {}
    if isinstance(changelog_cfg, dict) and changelog_cfg:
        lines.append("[changelog]")
        enabled = changelog_cfg.get("enabled")
        if isinstance(enabled, bool):
            lines.append(f"enabled = {'true' if enabled else 'false'}")
        actor = changelog_cfg.get("actor")
        if isinstance(actor, str) and actor.strip():
            lines.append(f"actor = {_toml_string(actor.strip())}")
        evaluator = changelog_cfg.get("evaluator")
        if isinstance(evaluator, str) and evaluator.strip():
            lines.append(f"evaluator = {_toml_string(evaluator.strip())}")
        model = changelog_cfg.get("model")
        if isinstance(model, str) and model.strip():
            lines.append(f"model = {_toml_string(model.strip())}")
        tokens = changelog_cfg.get("claude_thinking_tokens")
        if isinstance(tokens, int) and tokens > 0:
            lines.append(f"claude_thinking_tokens = {tokens}")
        if lines and lines[-1] != "":
            lines.append("")

    content = "\n".join(lines).rstrip() + "\n"
    return content if content.strip() else ""


def _ensure_gitignore_ignores(project_root: Path, pattern: str) -> None:
    path = project_root / ".gitignore"
    existing = ""
    try:
        existing = path.read_text(encoding="utf-8")
    except OSError:
        existing = ""
    lines = existing.splitlines()
    if any(line.strip() == pattern for line in lines):
        return
    if existing and not existing.endswith("\n"):
        existing += "\n"
    existing += f"{pattern}\n"
    path.write_text(existing, encoding="utf-8")


def _slugify_actor(actor: str) -> str:
    if not isinstance(actor, str):
        return "unknown"
    value = actor.strip().lower()
    if not value:
        return "unknown"
    value = value.replace("@", "-at-")
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    value = value.strip("-._")
    return value or "unknown"


def _changelog_paths(*, changelog_dir: Path, actor: str) -> tuple[Path, Path]:
    actor_slug = _slugify_actor(actor)
    base = changelog_dir / actor_slug
    return base / "entries.jsonl", base / "failures.jsonl"


def _parse_apply_patch_file_ops(patch_text: str) -> dict:
    """Extract file operations from an apply_patch payload."""
    result = {
        "created": set(),
        "modified": set(),
        "deleted": set(),
        "moved": [],  # list of {from,to}
    }
    if not isinstance(patch_text, str) or not patch_text.strip():
        return result

    current_path = None
    for raw in patch_text.splitlines():
        line = raw.strip()
        if line.startswith("*** Add File: "):
            current_path = line[len("*** Add File: ") :].strip()
            if current_path:
                result["created"].add(current_path)
            continue
        if line.startswith("*** Update File: "):
            current_path = line[len("*** Update File: ") :].strip()
            if current_path:
                result["modified"].add(current_path)
            continue
        if line.startswith("*** Delete File: "):
            current_path = line[len("*** Delete File: ") :].strip()
            if current_path:
                result["deleted"].add(current_path)
            continue
        if line.startswith("*** Move to: "):
            dest = line[len("*** Move to: ") :].strip()
            if current_path and dest:
                result["moved"].append({"from": current_path, "to": dest})
            continue

    return result


def _truncate_text(value: str, max_chars: int) -> str:
    if not isinstance(value, str):
        return ""
    if max_chars <= 0:
        return ""
    value = value.strip()
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3].rstrip() + "..."


def _truncate_text_middle(value: str, max_chars: int) -> str:
    if not isinstance(value, str):
        return ""
    if max_chars <= 0:
        return ""
    value = value.strip()
    if len(value) <= max_chars:
        return value
    glue = "\n...\n"
    if max_chars <= len(glue) + 10:
        return _truncate_text(value, max_chars)
    head_len = (max_chars - len(glue)) // 2
    tail_len = max_chars - len(glue) - head_len
    return value[:head_len].rstrip() + glue + value[-tail_len:].lstrip()


def _truncate_text_tail(value: str, max_chars: int) -> str:
    if not isinstance(value, str):
        return ""
    if max_chars <= 0:
        return ""
    value = value.strip()
    if len(value) <= max_chars:
        return value
    return "..." + value[-(max_chars - 3) :].lstrip()


def _strip_digest_json_block(text: str) -> str:
    if not isinstance(text, str):
        return ""
    if "DIGEST_JSON_START" not in text and "DIGEST_JSON_END" not in text:
        return text

    # Full block present.
    text = re.sub(
        r"DIGEST_JSON_START.*?DIGEST_JSON_END",
        "DIGEST_JSON_[REDACTED]",
        text,
        flags=re.DOTALL,
    )

    # Start marker present but end marker missing (truncated output).
    text = re.sub(
        r"DIGEST_JSON_START.*",
        "DIGEST_JSON_[REDACTED]",
        text,
        flags=re.DOTALL,
    )

    # End marker present but start marker missing (we captured the tail).
    if "DIGEST_JSON_START" not in text and "DIGEST_JSON_END" in text:
        after = text.split("DIGEST_JSON_END", 1)[1]
        text = "DIGEST_JSON_[REDACTED]\n" + after.lstrip()

    return text


def _extract_text_blocks_from_message(message: dict) -> list[str]:
    """Return plaintext text blocks from a normalized message dict."""
    if not isinstance(message, dict):
        return []
    content = message.get("content", "")
    if isinstance(content, str):
        txt = content.strip()
        return [txt] if txt else []
    if not isinstance(content, list):
        return []
    texts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") != "text":
            continue
        txt = block.get("text", "")
        if isinstance(txt, str) and txt.strip():
            texts.append(txt.strip())
    return texts


def _extract_tool_blocks_from_message(message: dict) -> list[dict]:
    if not isinstance(message, dict):
        return []
    content = message.get("content", "")
    if not isinstance(content, list):
        return []
    blocks: list[dict] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") in ("tool_use", "tool_result"):
            blocks.append(block)
    return blocks


def _tool_name_is_command(tool_name: str) -> bool:
    if not isinstance(tool_name, str):
        return False
    if tool_name in ("bash", "shell", "terminal"):
        return True
    return tool_name.endswith(".exec_command") or tool_name.endswith("exec_command")


def _tool_name_is_apply_patch(tool_name: str) -> bool:
    if not isinstance(tool_name, str):
        return False
    return tool_name.endswith(".apply_patch") or tool_name.endswith("apply_patch")


def _extract_patch_text(tool_input) -> str | None:
    if isinstance(tool_input, str):
        return tool_input
    if not isinstance(tool_input, dict):
        return None
    for key in ("patch", "arguments"):
        val = tool_input.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return None


def _extract_path_from_tool_input(tool_input) -> str | None:
    if not isinstance(tool_input, dict):
        return None
    for key in ("path", "file_path", "filepath", "filename"):
        val = tool_input.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _extract_cmd_from_tool_input(tool_input) -> str | None:
    if not isinstance(tool_input, dict):
        return None
    for key in ("cmd", "command"):
        val = tool_input.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _summarize_tool_input(tool_input, *, max_chars: int = 4000):
    if isinstance(tool_input, str):
        return _truncate_text(tool_input, max_chars)
    if isinstance(tool_input, dict):
        summary = {}
        for k, v in tool_input.items():
            if isinstance(v, str):
                summary[k] = _truncate_text(v, max_chars)
            elif isinstance(v, (dict, list)):
                summary[k] = _truncate_text(json.dumps(v, ensure_ascii=False), max_chars)
            else:
                summary[k] = v
        return summary
    if isinstance(tool_input, list):
        return _truncate_text(json.dumps(tool_input, ensure_ascii=False), max_chars)
    return tool_input


def _looks_like_test_command(cmd: str) -> bool:
    if not isinstance(cmd, str):
        return False
    cmd = cmd.strip()
    if not cmd:
        return False
    patterns = [
        r"\\bpytest\\b",
        r"\\buv\\s+run\\b.*\\bpytest\\b",
        r"\\bnpm\\s+test\\b",
        r"\\byarn\\s+test\\b",
        r"\\bpnpm\\s+test\\b",
        r"\\bgo\\s+test\\b",
        r"\\bmvn\\b.*\\btest\\b",
        r"\\bgradle\\b.*\\btest\\b",
        r"\\brake\\s+test\\b",
    ]
    return any(re.search(pat, cmd) for pat in patterns)


def _extract_commits_from_text(text: str) -> list[dict]:
    commits: list[dict] = []
    if not isinstance(text, str) or not text:
        return commits
    for m in COMMIT_PATTERN.finditer(text):
        commits.append({"hash": m.group(1), "message": m.group(2)})
    return commits


def _build_changelog_digest(
    *,
    source_jsonl: Path,
    start: str,
    end: str,
    prior_prompts: int = 3,
) -> dict:
    start_dt = _parse_iso8601(start)
    end_dt = _parse_iso8601(end)
    if start_dt is None or end_dt is None:
        raise click.ClickException("Invalid start/end timestamps for changelog digest")

    session_data = parse_session_file(source_jsonl)
    loglines = session_data.get("loglines", [])
    source_format = session_data.get("source_format") or "unknown"

    before: list[dict] = []
    within: list[dict] = []

    for entry in loglines:
        if not isinstance(entry, dict):
            continue
        ts = entry.get("timestamp", "")
        dt = _parse_iso8601(ts)
        if dt is None:
            continue
        if dt < start_dt:
            before.append(entry)
        elif dt > end_dt:
            continue
        else:
            within.append(entry)

    prior_user_prompts: list[dict] = []
    for entry in before:
        if entry.get("type") != "user":
            continue
        msg = entry.get("message") if isinstance(entry.get("message"), dict) else {}
        content = msg.get("content", "")
        text = extract_text_from_content(content)
        if not text:
            continue
        if text.startswith("Stop hook feedback:"):
            continue
        prior_user_prompts.append(
            {
                "timestamp": entry.get("timestamp", ""),
                "text": _truncate_text(text, 2000),
            }
        )
    if prior_prompts > 0:
        prior_user_prompts = prior_user_prompts[-prior_prompts:]

    delta_user_prompts: list[dict] = []
    delta_assistant_text: list[dict] = []
    tool_calls: list[dict] = []
    tool_errors: list[dict] = []
    commits: list[dict] = []
    tests: list[dict] = []

    touched_created: set[str] = set()
    touched_modified: set[str] = set()
    touched_deleted: set[str] = set()
    touched_moved: list[dict] = []

    tool_calls_by_id: dict[str, dict] = {}
    fallback_tool_calls: list[dict] = []

    for entry in within:
        entry_type = entry.get("type")
        ts = entry.get("timestamp", "")
        msg = entry.get("message") if isinstance(entry.get("message"), dict) else {}

        if entry_type == "user":
            content = msg.get("content", "")
            text = extract_text_from_content(content)
            if text and not text.startswith("Stop hook feedback:"):
                delta_user_prompts.append({"timestamp": ts, "text": _truncate_text(text, 2000)})
            continue

        if entry_type != "assistant":
            continue

        for txt in _extract_text_blocks_from_message(msg):
            commits.extend(_extract_commits_from_text(txt))
            delta_assistant_text.append({"timestamp": ts, "text": _truncate_text(txt, 2000)})

        for block in _extract_tool_blocks_from_message(msg):
            btype = block.get("type")
            if btype == "tool_use":
                tool_name = block.get("name") or "unknown"
                tool_input = block.get("input")
                tool_id = block.get("id")
                input_summary = _summarize_tool_input(tool_input)
                if _tool_name_is_apply_patch(tool_name) and isinstance(input_summary, dict):
                    for k in ("patch", "arguments"):
                        if k in input_summary:
                            input_summary[k] = "[omitted]"
                call = {
                    "timestamp": ts,
                    "tool": tool_name,
                    "input": input_summary,
                    "result": None,
                }
                if isinstance(tool_id, str) and tool_id:
                    call["id"] = tool_id

                if _tool_name_is_apply_patch(tool_name):
                    patch_text = _extract_patch_text(tool_input)
                    if patch_text:
                        file_ops = _parse_apply_patch_file_ops(patch_text)
                        touched_created |= set(file_ops["created"])
                        touched_modified |= set(file_ops["modified"])
                        touched_deleted |= set(file_ops["deleted"])
                        touched_moved.extend(file_ops["moved"])
                        call["patch_snippet"] = _truncate_text(patch_text, 12000)
                        patch_files: set[str] = (
                            set(file_ops["created"]) | set(file_ops["modified"]) | set(file_ops["deleted"])
                        )
                        for mv in file_ops["moved"]:
                            if not isinstance(mv, dict):
                                continue
                            for k in ("from", "to"):
                                v = mv.get(k)
                                if isinstance(v, str) and v.strip():
                                    patch_files.add(v.strip())
                        if patch_files:
                            call["patch_files"] = sorted(patch_files)

                path_hint = _extract_path_from_tool_input(tool_input)
                if path_hint:
                    touched_modified.add(path_hint)
                    call["path_hint"] = path_hint

                cmd_hint = None
                if _tool_name_is_command(tool_name):
                    cmd_hint = _extract_cmd_from_tool_input(tool_input)
                    if cmd_hint:
                        call["cmd"] = _truncate_text(cmd_hint, 500)
                        if _looks_like_test_command(cmd_hint):
                            call["is_test"] = True
                tool_calls.append(call)
                if isinstance(tool_id, str) and tool_id:
                    tool_calls_by_id[tool_id] = call
                fallback_tool_calls.append(call)
                continue

            if btype == "tool_result":
                content = block.get("content", "")
                if isinstance(content, (dict, list)):
                    content_text = json.dumps(content, ensure_ascii=False)
                else:
                    content_text = str(content)

                commits.extend(_extract_commits_from_text(content_text))

                is_error = block.get("is_error")
                if is_error is None:
                    is_error = _infer_is_error_from_exec_output(content_text)

                exit_code = None
                tool_use_id = block.get("tool_use_id")
                target_call = None
                if isinstance(tool_use_id, str) and tool_use_id:
                    target_call = tool_calls_by_id.get(tool_use_id)
                if target_call is None:
                    for candidate in reversed(fallback_tool_calls):
                        if candidate.get("result") is None:
                            target_call = candidate
                            break
                if target_call is not None and target_call.get("cmd"):
                    exit_code = _infer_exit_code_from_exec_output(content_text)

                result_obj = {
                    "timestamp": ts,
                    "is_error": bool(is_error),
                }
                if exit_code is not None:
                    result_obj["exit_code"] = exit_code
                if is_error:
                    # Keep command output only for errors (short tail for debugging).
                    result_obj["content_snippet"] = _truncate_text_tail(content_text, 4000)

                if target_call is not None and target_call.get("result") is None:
                    target_call["result"] = result_obj
                    if target_call.get("is_test") and target_call.get("cmd"):
                        if block.get("is_error") is True:
                            test_result = "fail"
                        elif exit_code == 0:
                            test_result = "pass"
                        elif exit_code is None:
                            test_result = "unknown"
                        else:
                            test_result = "fail"
                        tests.append(
                            {
                                "cmd": target_call["cmd"],
                                "result": test_result,
                            }
                        )
                if is_error:
                    tool_errors.append(result_obj)
                continue

    touched_files = {
        "created": sorted(touched_created),
        "modified": sorted(touched_modified),
        "deleted": sorted(touched_deleted),
        "moved": touched_moved,
    }

    # Keep assistant text short: include only the last few snippets.
    delta_assistant_text = delta_assistant_text[-8:]

    return {
        "schema_version": 1,
        "source_format": source_format,
        "window": {"start": start, "end": end},
        "context": {"prior_user_prompts": prior_user_prompts},
        "delta": {
            "user_prompts": delta_user_prompts,
            "assistant_text": delta_assistant_text,
            "tool_calls": tool_calls,
            "tool_errors": tool_errors,
            "touched_files": touched_files,
            "tests": tests,
            "commits": commits[:50],
        },
    }


_BUDGET_DIGEST_DEFAULT_MAX_CHARS = 200_000


def _touched_file_tokens_for_budget(touched_files: dict) -> set[str]:
    if not isinstance(touched_files, dict):
        return set()
    tokens: set[str] = set()

    def _add_path(path: str):
        p = path.replace("\\", "/").lower().strip()
        if not p:
            return
        base = p.rsplit("/", 1)[-1]
        if base:
            tokens.add(base)
            stem = base.split(".", 1)[0]
            if stem and stem != base:
                tokens.add(stem)

    for k in ("created", "modified", "deleted"):
        for v in touched_files.get(k, []) if isinstance(touched_files.get(k), list) else []:
            if isinstance(v, str):
                _add_path(v)
    moved = touched_files.get("moved")
    if isinstance(moved, list):
        for mv in moved:
            if not isinstance(mv, dict):
                continue
            for k in ("from", "to"):
                v = mv.get(k)
                if isinstance(v, str):
                    _add_path(v)

    # Keep only short-ish tokens to avoid pathological scoring loops.
    return {t for t in tokens if 1 <= len(t) <= 64 and "/" not in t}


_BUDGET_USER_KEYWORDS = (
    "fix",
    "bug",
    "refactor",
    "rename",
    "migrate",
    "upgrade",
    "security",
    "perf",
    "optimiz",
    "test",
    "failing",
    "error",
    "changelog",
)


def _score_budget_text(text: str, *, tokens: set[str]) -> int:
    if not isinstance(text, str) or not text:
        return 0
    lower = text.lower()
    score = 0
    for kw in _BUDGET_USER_KEYWORDS:
        if kw in lower:
            score += 2
    for tok in tokens:
        if tok in lower:
            score += 5
    return score


def _select_budget_items(
    items: list[dict],
    *,
    max_items: int,
    always_head: int,
    always_tail: int,
    score_fn,
) -> list[dict]:
    if not isinstance(items, list) or max_items <= 0:
        return []
    if len(items) <= max_items:
        return items

    keep: set[int] = set()
    for i in range(min(always_head, len(items))):
        keep.add(i)
    for i in range(max(0, len(items) - always_tail), len(items)):
        keep.add(i)

    remaining = max_items - len(keep)
    if remaining > 0:
        scored: list[tuple[int, int]] = []
        for i, item in enumerate(items):
            if i in keep:
                continue
            try:
                scored.append((int(score_fn(item)), i))
            except Exception:
                scored.append((0, i))
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        for _, i in scored[:remaining]:
            keep.add(i)

    return [items[i] for i in sorted(keep)]


def _slim_tool_call_for_budget(call: dict) -> dict:
    out = {
        "timestamp": call.get("timestamp", ""),
        "tool": call.get("tool", "unknown"),
    }

    for k in ("cmd", "is_test", "path_hint", "patch_files"):
        if k in call:
            out[k] = call.get(k)

    res = call.get("result")
    if isinstance(res, dict):
        is_err = bool(res.get("is_error"))
        res_out = {"timestamp": res.get("timestamp", ""), "is_error": is_err}
        if "exit_code" in res and res.get("exit_code") is not None:
            res_out["exit_code"] = res.get("exit_code")
        if is_err and isinstance(res.get("content_snippet"), str) and res.get("content_snippet"):
            res_out["content_snippet"] = res.get("content_snippet")
        if res_out.get("is_error") or ("exit_code" in res_out):
            out["result"] = res_out

    return out


def _budget_changelog_digest_once(
    digest: dict,
    *,
    max_user_prompts: int = 30,
    max_tool_calls: int = 200,
    max_assistant_text: int = 4,
    max_tool_errors: int = 20,
) -> dict:
    if not isinstance(digest, dict):
        return {"schema_version": 1, "digest_mode": "budget", "delta": {}}

    # Work off the already-parsed digest to avoid re-reading large transcripts.
    out = json.loads(json.dumps(digest, ensure_ascii=False))
    out["digest_mode"] = "budget"

    delta = out.get("delta") if isinstance(out.get("delta"), dict) else {}
    touched = delta.get("touched_files") if isinstance(delta.get("touched_files"), dict) else {}
    tokens = _touched_file_tokens_for_budget(touched)

    # Prompts: keep head/tail + highest-signal middle prompts.
    prompts = delta.get("user_prompts") if isinstance(delta.get("user_prompts"), list) else []

    def _prompt_score(item: dict) -> int:
        return _score_budget_text(item.get("text", ""), tokens=tokens) if isinstance(item, dict) else 0

    delta["user_prompts"] = _select_budget_items(
        prompts,
        max_items=max_user_prompts,
        always_head=5,
        always_tail=10,
        score_fn=_prompt_score,
    )

    # Assistant text: keep last few snippets only.
    assistant_text = delta.get("assistant_text") if isinstance(delta.get("assistant_text"), list) else []
    if max_assistant_text > 0:
        delta["assistant_text"] = assistant_text[-max_assistant_text:]
    else:
        delta["assistant_text"] = []

    # Tool errors: keep last N (these already include output only for errors).
    tool_errors = delta.get("tool_errors") if isinstance(delta.get("tool_errors"), list) else []
    if max_tool_errors > 0:
        delta["tool_errors"] = tool_errors[-max_tool_errors:]
    else:
        delta["tool_errors"] = []

    # Tool calls: keep the most informative calls and drop bulky input/patch text.
    tool_calls = delta.get("tool_calls") if isinstance(delta.get("tool_calls"), list) else []

    def _call_score(item: dict) -> int:
        if not isinstance(item, dict):
            return 0
        score = 0
        tool = item.get("tool", "")
        if _tool_name_is_apply_patch(tool):
            score += 80
        if item.get("is_test") is True:
            score += 70
        cmd = item.get("cmd")
        if isinstance(cmd, str) and cmd.strip().startswith("git "):
            score += 60
        res = item.get("result") if isinstance(item.get("result"), dict) else {}
        if res.get("is_error") is True:
            score += 100
        if item.get("patch_files"):
            score += 15
        if item.get("path_hint"):
            score += 10
        return score

    selected = _select_budget_items(
        tool_calls,
        max_items=max_tool_calls,
        always_head=10,
        always_tail=10,
        score_fn=_call_score,
    )

    delta["tool_calls"] = [_slim_tool_call_for_budget(c) for c in selected if isinstance(c, dict)]
    out["delta"] = delta

    return out


def _budget_changelog_digest(
    digest: dict,
    *,
    max_chars: int = _BUDGET_DIGEST_DEFAULT_MAX_CHARS,
    max_user_prompts: int = 30,
    max_tool_calls: int = 200,
    max_assistant_text: int = 4,
    max_tool_errors: int = 20,
) -> dict:
    budget_user = max_user_prompts
    budget_calls = max_tool_calls
    budget_assistant = max_assistant_text
    budget_errors = max_tool_errors

    last = _budget_changelog_digest_once(
        digest,
        max_user_prompts=budget_user,
        max_tool_calls=budget_calls,
        max_assistant_text=budget_assistant,
        max_tool_errors=budget_errors,
    )
    for _ in range(6):
        try:
            size = len(json.dumps(last, ensure_ascii=False))
        except Exception:
            size = max_chars + 1
        if size <= max_chars:
            break

        if budget_calls > 50:
            budget_calls = max(50, budget_calls // 2)
        elif budget_user > 15:
            budget_user = max(15, budget_user - 5)
        elif budget_assistant > 2:
            budget_assistant = 2
        elif budget_errors > 10:
            budget_errors = 10
        else:
            break

        last = _budget_changelog_digest_once(
            digest,
            max_user_prompts=budget_user,
            max_tool_calls=budget_calls,
            max_assistant_text=budget_assistant,
            max_tool_errors=budget_errors,
        )

    return last


def _format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    mins, secs = divmod(total, 60)
    hours, mins = divmod(mins, 60)
    if hours:
        return f"{hours}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"


def _append_log_line(path: Path | None, message: str) -> None:
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    ts = _now_iso8601()
    line = f"{ts} {message.strip()}\n"
    try:
        with _LOG_IO_LOCK:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
    except OSError:
        return


def _backfill_log_path(*, project_root: Path, actor: str | None, evaluator: str) -> Path | None:
    raw_dir = _env_first("CTX_LOG_DIR", "AI_CODE_SESSIONS_LOG_DIR")
    if isinstance(raw_dir, str) and raw_dir.strip():
        if raw_dir.strip().lower() in ("0", "false", "none", "off"):
            return None
        base = Path(raw_dir.strip()).expanduser()
        if not base.is_absolute():
            base = (project_root / base).resolve()
        log_dir = base
    else:
        log_dir = project_root / ".logs" / "ai-code-sessions"

    actor_slug = _slugify_actor(actor or _detect_actor(project_root=project_root))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return log_dir / f"changelog-backfill-{actor_slug}-{evaluator}-{stamp}.log"


def _run_with_activity_indicator(*, label: str, fn, interval_seconds: float = 1.0):
    if os.environ.get("CTX_CHANGELOG_PROGRESS") == "0":
        return fn()
    if not sys.stderr.isatty():
        return fn()

    stop = threading.Event()
    start = time.monotonic()

    def _line(prefix: str) -> str:
        elapsed = _format_elapsed(time.monotonic() - start)
        return f"{label} {prefix} {elapsed}"

    def _worker() -> None:
        for ch in itertools.cycle("|/-\\"):
            if stop.wait(interval_seconds):
                break
            try:
                sys.stderr.write("\r" + _line(ch))
                sys.stderr.flush()
            except Exception:
                break

    sys.stderr.write(_line("|"))
    sys.stderr.flush()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    try:
        return fn()
    finally:
        stop.set()
        t.join(timeout=2)
        sys.stderr.write("\r" + _line("done") + "\n")
        sys.stderr.flush()


def _run_codex_changelog_evaluator(
    *, prompt: str, schema_path: Path, cd: Path | None = None, model: str | None = None
) -> dict:
    codex_bin = shutil.which("codex")
    if not codex_bin:
        raise click.ClickException("codex CLI not found on PATH (required for changelog generation)")

    out_file = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".codex-last-message.json", delete=False)
    out_path = Path(out_file.name)
    out_file.close()

    temp_codex_home: tempfile.TemporaryDirectory[str] | None = None
    env = os.environ.copy()
    try:
        temp_codex_home = tempfile.TemporaryDirectory(prefix="ai-code-sessions-codex-home-")
        temp_home_path = Path(temp_codex_home.name)
        env["CODEX_HOME"] = str(temp_home_path)

        # Seed auth.json from the user's existing Codex home, if present, so a
        # headless `codex exec` can authenticate without additional prompts.
        try:
            src_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")).expanduser()
            auth_src = src_home / "auth.json"
            auth_dest = temp_home_path / "auth.json"
            if auth_src.is_file() and not auth_dest.exists():
                shutil.copy2(auth_src, auth_dest)
                try:
                    os.chmod(auth_dest, 0o600)
                except OSError:
                    pass
        except Exception:
            pass

    except Exception:
        # If we can't create an isolated CODEX_HOME for any reason, fall back to
        # the default environment (Codex may still work in non-sandboxed runs).
        if temp_codex_home is not None:
            try:
                temp_codex_home.cleanup()
            except Exception:
                pass
        temp_codex_home = None

    # Defaults for headless changelog evaluation.
    model = model or "gpt-5.2"

    cmd = [
        codex_bin,
        "exec",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(out_path),
        "-",
    ]
    if model:
        cmd[2:2] = ["-m", model]
    # Prefer high reasoning for changelog distillation (can be overridden via --model).
    cmd[2:2] = ["-c", 'model_reasoning_effort="xhigh"']
    if cd:
        cmd[2:2] = ["-C", str(cd)]

    proc = subprocess.run(
        cmd,
        input=prompt,
        text=True,
        capture_output=True,
        env=env,
    )
    try:
        if proc.returncode != 0:
            stderr_sanitized = _strip_digest_json_block(proc.stderr)
            stdout_sanitized = _strip_digest_json_block(proc.stdout)
            stderr_tail = _truncate_text_tail(stderr_sanitized, 4000)
            stdout_tail = _truncate_text_tail(stdout_sanitized, 2000)
            details = []
            if stderr_tail:
                details.append(f"stderr_tail: {stderr_tail}")
            if stdout_tail:
                details.append(f"stdout_tail: {stdout_tail}")
            suffix = ("\n" + "\n".join(details)) if details else ""
            raise click.ClickException(f"codex exec failed (exit {proc.returncode}).{suffix}")

        try:
            raw = out_path.read_text(encoding="utf-8").strip()
        except OSError as e:
            raise click.ClickException(f"Failed reading codex output: {e}")
    finally:
        try:
            out_path.unlink()
        except OSError:
            pass
        if temp_codex_home is not None:
            try:
                temp_codex_home.cleanup()
            except Exception:
                pass

    if not raw:
        raise click.ClickException("codex output was empty")

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Some versions may wrap JSON in markdown; attempt a minimal salvage.
        m = re.search(r"\\{.*\\}", raw, flags=re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        raise click.ClickException("codex output was not valid JSON")


_CLAUDE_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    return _CLAUDE_ANSI_RE.sub("", text or "")


def _extract_json_object(text: str) -> dict:
    s = (text or "").strip()
    s = _strip_ansi(s).strip()
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end < 0 or end <= start:
        raise click.ClickException("Claude output did not contain a JSON object")
    try:
        obj = json.loads(s[start : end + 1])
    except json.JSONDecodeError as e:
        raise click.ClickException(f"Claude output was not valid JSON ({e})")
    if not isinstance(obj, dict):
        raise click.ClickException("Claude output JSON was not an object")
    return obj


def _extract_json_from_result_string(result_text: str) -> dict:
    s = (result_text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\\s*", "", s.strip(), flags=re.IGNORECASE)
        s = re.sub(r"\\s*```\\s*$", "", s.strip())
    return _extract_json_object(s)


def _run_claude_changelog_evaluator(
    *,
    prompt: str,
    json_schema: dict,
    cd: Path | None = None,
    model: str | None = None,
    max_thinking_tokens: int | None = None,
    timeout_seconds: int = 900,
) -> dict:
    exe = shutil.which("claude")
    if not exe:
        raise click.ClickException(
            "Claude Code CLI ('claude') not found on PATH (required for changelog evaluation). "
            "Install and authenticate Claude Code, then retry."
        )

    model = model or "opus"
    max_thinking_tokens = 8192 if max_thinking_tokens is None else max_thinking_tokens

    args: list[str] = [
        exe,
        "--print",
        "--no-session-persistence",
        "--output-format",
        "json",
        "--json-schema",
        json.dumps(json_schema, ensure_ascii=False),
        "--permission-mode",
        "dontAsk",
        "--tools",
        "",
        "--model",
        model,
        "--max-thinking-tokens",
        str(max_thinking_tokens),
        prompt,
    ]

    try:
        proc = subprocess.run(
            args,
            cwd=str(cd) if cd else None,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
        )
    except OSError as e:
        raise click.ClickException(f"claude failed to start: {e}")

    stdout = _strip_ansi(proc.stdout or "").strip()
    stderr = _strip_ansi(proc.stderr or "").strip()
    if proc.returncode != 0:
        stderr_tail = _truncate_text_tail(stderr, 4000)
        stdout_tail = _truncate_text_tail(stdout, 2000)
        details = []
        if stderr_tail:
            details.append(f"stderr_tail: {stderr_tail}")
        if stdout_tail:
            details.append(f"stdout_tail: {stdout_tail}")
        suffix = ("\n" + "\n".join(details)) if details else ""
        raise click.ClickException(f"claude failed (exit {proc.returncode}).{suffix}")

    resp = _extract_json_object(stdout)
    if bool(resp.get("is_error")):
        raise click.ClickException(f"claude returned is_error=true. raw_response={_truncate_text(str(resp), 2000)}")

    structured = resp.get("structured_output")
    if isinstance(structured, dict):
        return structured

    result_text = resp.get("result")
    if isinstance(result_text, str) and result_text.strip():
        structured2 = _extract_json_from_result_string(result_text)
        if isinstance(structured2, dict):
            return structured2

    raise click.ClickException(
        "Claude did not return structured_output and no JSON could be parsed from result text. "
        f"raw_response_keys={list(resp.keys())}"
    )


def _build_codex_changelog_prompt(*, digest: dict) -> str:
    return (
        "You are generating an engineering changelog entry for a single terminal-based coding session.\n"
        "\n"
        "Requirements:\n"
        "- Focus ONLY on work done within the provided time window (the 'delta').\n"
        "- Do NOT quote user prompts verbatim; paraphrase context into searchable phrasing.\n"
        "- Do NOT include secrets, tokens, API keys, or credentials. If unsure, write [REDACTED].\n"
        "- Be concrete: mention what changed and why, and reference files by path when known.\n"
        "- Keep it concise.\n"
        "\n"
        "Return JSON matching the output schema.\n"
        "\n"
        "DIGEST_JSON_START\n"
        f"{json.dumps(digest, ensure_ascii=False, indent=2)}\n"
        "DIGEST_JSON_END\n"
    )


def _write_changelog_failure(
    *,
    changelog_dir: Path,
    run_id: str,
    tool: str,
    actor: str,
    project: str,
    project_root: Path,
    session_dir: Path,
    start: str,
    end: str,
    error: str,
    source_jsonl: Path | None,
    source_match_json: Path | None,
) -> None:
    payload = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": _now_iso8601(),
        "tool": tool or "unknown",
        "actor": actor,
        "project": project,
        "project_root": str(project_root),
        "session_dir": str(session_dir),
        "start": start,
        "end": end,
        "source_jsonl": str(source_jsonl) if source_jsonl else None,
        "source_match_json": str(source_match_json) if source_match_json else None,
        "error": _truncate_text_middle(error, 2000),
    }
    _, failures_path = _changelog_paths(changelog_dir=changelog_dir, actor=actor)
    _append_jsonl(failures_path, payload)


def _looks_like_usage_limit_error(error_text: str) -> bool:
    if not isinstance(error_text, str) or not error_text:
        return False
    lower = error_text.lower()
    return (
        "usage_limit_reached" in lower
        or "you've hit your usage limit" in lower
        or "too many requests" in lower
        or "rate_limit_exceeded" in lower
        or "rate_limit_reached" in lower
        or "rate_limit_hit" in lower
        or "rate limit exceeded" in lower
        or "rate limit reached" in lower
        or "rate limit hit" in lower
        or ("429" in re.findall(r"[0-9]+", lower))
    )


def _looks_like_context_window_error(error_text: str) -> bool:
    if not isinstance(error_text, str) or not error_text:
        return False
    lower = error_text.lower()
    return (
        "argument list too long" in lower
        or ("context window" in lower and ("ran out of room" in lower or "start a new conversation" in lower))
        or ("context length" in lower and ("exceeded" in lower or "too long" in lower))
        or ("prompt" in lower and "too long" in lower)
    )


def _generate_and_append_changelog_entry(
    *,
    tool: str,
    label: str | None,
    cwd: str,
    project_root: Path,
    session_dir: Path,
    start: str,
    end: str,
    source_jsonl: Path,
    source_match_json: Path,
    prior_prompts: int = 3,
    actor: str | None = None,
    evaluator: str = "codex",
    evaluator_model: str | None = None,
    claude_max_thinking_tokens: int | None = None,
    continuation_of_run_id: str | None = None,
    halt_on_429: bool = False,
) -> tuple[bool, str | None, str]:
    changelog_dir = project_root / ".changelog"

    project = project_root.name or str(project_root)
    actor_value = actor or _detect_actor(project_root=project_root)
    actor_slug = _slugify_actor(actor_value)
    entries_path, _ = _changelog_paths(changelog_dir=changelog_dir, actor=actor_value)
    session_dir_abs = session_dir.resolve()
    run_id = _compute_run_id(
        tool=tool,
        start=start,
        end=end,
        session_dir=session_dir_abs,
        source_jsonl=source_jsonl,
    )

    existing = (
        _load_existing_run_ids(entries_path)
        | _load_existing_run_ids(changelog_dir / "entries.jsonl")
        | _load_existing_run_ids(changelog_dir / "actors" / actor_slug / "entries.jsonl")
    )
    if run_id in existing:
        return False, run_id, "exists"

    try:
        module_override = sys.modules.get("ai_code_sessions")
        build_digest = _build_changelog_digest
        run_codex_eval = _run_codex_changelog_evaluator
        run_claude_eval = _run_claude_changelog_evaluator
        if module_override is not None:
            build_digest = getattr(module_override, "_build_changelog_digest", build_digest)
            run_codex_eval = getattr(module_override, "_run_codex_changelog_evaluator", run_codex_eval)
            run_claude_eval = getattr(module_override, "_run_claude_changelog_evaluator", run_claude_eval)

        digest = build_digest(
            source_jsonl=source_jsonl,
            start=start,
            end=end,
            prior_prompts=prior_prompts,
        )

        evaluator_value = (evaluator or "codex").strip().lower()
        if evaluator_value not in ("codex", "claude"):
            raise click.ClickException(f"Unknown changelog evaluator: {evaluator}")

        schema_path: Path | None = None
        try:
            if evaluator_value == "codex":
                schema_path = _write_json_schema_tempfile(_CHANGELOG_CODEX_OUTPUT_SCHEMA)

            activity_label = f"Changelog eval ({evaluator_value}) {session_dir.name}"

            def _run_eval(d: dict) -> dict:
                prompt = _build_codex_changelog_prompt(digest=d)
                if evaluator_value == "codex":
                    if schema_path is None:
                        raise click.ClickException("Internal error: missing Codex schema path")
                    return _run_with_activity_indicator(
                        label=activity_label,
                        fn=lambda: run_codex_eval(
                            prompt=prompt,
                            schema_path=schema_path,
                            cd=project_root,
                            model=evaluator_model,
                        ),
                    )
                return _run_with_activity_indicator(
                    label=activity_label,
                    fn=lambda: run_claude_eval(
                        prompt=prompt,
                        json_schema=_CHANGELOG_CODEX_OUTPUT_SCHEMA,
                        cd=project_root,
                        model=evaluator_model,
                        max_thinking_tokens=claude_max_thinking_tokens,
                    ),
                )

            try:
                evaluator_out = _run_eval(digest)
            except Exception as e:
                # Some sessions are too large to fit in a single evaluator prompt.
                # Retry once with a budgeted digest before recording a failure.
                if isinstance(e, subprocess.TimeoutExpired) or "timed out after" in str(e).lower():
                    click.echo("Changelog eval timed out; retrying with budget digest...", err=True)
                    evaluator_out = _run_eval(_budget_changelog_digest(digest, max_chars=100_000))
                elif _looks_like_context_window_error(str(e)):
                    click.echo("Changelog eval too large; retrying with budget digest...", err=True)
                    evaluator_out = _run_eval(_budget_changelog_digest(digest))
                else:
                    raise
        finally:
            if schema_path is not None:
                try:
                    schema_path.unlink()
                except OSError:
                    pass

        summary = evaluator_out.get("summary")
        bullets = evaluator_out.get("bullets")
        tags = evaluator_out.get("tags")
        notes = evaluator_out.get("notes")

        if not isinstance(summary, str) or not summary.strip():
            raise click.ClickException(f"{evaluator_value} output missing summary")
        if not isinstance(bullets, list) or not bullets:
            raise click.ClickException(f"{evaluator_value} output missing bullets")
        if not isinstance(tags, list):
            tags = []

        created_at_dt = _parse_iso8601(start) or _parse_iso8601(end)
        created_at = created_at_dt.isoformat() if created_at_dt else _now_iso8601()

        index_html_path = session_dir_abs / "index.html"
        if not index_html_path.exists():
            trace_path = session_dir_abs / "trace.html"
            if trace_path.exists():
                index_html_path = trace_path

        entry = {
            "schema_version": CHANGELOG_ENTRY_SCHEMA_VERSION,
            "run_id": run_id,
            "created_at": created_at,
            "tool": tool or "unknown",
            "actor": actor_value,
            "project": project,
            "project_root": str(project_root),
            "label": label,
            "start": start,
            "end": end,
            "session_dir": str(session_dir_abs),
            "continuation_of_run_id": continuation_of_run_id,
            "transcript": {
                "output_dir": str(session_dir_abs),
                "index_html": str(index_html_path.resolve()),
                "source_jsonl": str(source_jsonl),
                "source_match_json": str(source_match_json),
            },
            "summary": summary.strip(),
            "bullets": [str(b).strip() for b in bullets if str(b).strip()][:12],
            "tags": [str(t).strip() for t in tags if str(t).strip()][:24],
            "touched_files": digest.get("delta", {}).get(
                "touched_files", {"created": [], "modified": [], "deleted": [], "moved": []}
            ),
            "tests": digest.get("delta", {}).get("tests", []),
            "commits": digest.get("delta", {}).get("commits", []),
            "notes": notes.strip() if isinstance(notes, str) and notes.strip() else None,
        }

        _append_jsonl(entries_path, entry)
        return True, run_id, "appended"
    except Exception as e:
        error_text = _strip_digest_json_block(str(e))
        _write_changelog_failure(
            changelog_dir=changelog_dir,
            run_id=run_id,
            tool=tool,
            actor=actor_value,
            project=project,
            project_root=project_root,
            session_dir=session_dir,
            start=start,
            end=end,
            error=error_text,
            source_jsonl=source_jsonl,
            source_match_json=source_match_json,
        )
        if halt_on_429 and _looks_like_usage_limit_error(error_text):
            return False, run_id, "rate_limited"
        return False, run_id, "failed"


def _derive_label_from_session_dir(session_dir: Path) -> str | None:
    name = session_dir.name
    if "_" not in name:
        return None
    # ctx uses <STAMP>_<SANITIZED_TITLE>[_N]
    label_part = name.split("_", 1)[1]
    # Drop trailing _N suffix if present.
    m = re.match(r"^(.*?)(?:_\\d+)?$", label_part)
    if m and m.group(1):
        label_part = m.group(1)
    label_part = label_part.replace("_", " ").strip()
    return label_part or None


def _parse_ctx_stamp_from_dir(session_dir: Path, tz_name: str | None = None) -> datetime | None:
    name = session_dir.name
    if "_" in name:
        stamp = name.split("_", 1)[0]
    else:
        stamp = name
    try:
        dt = datetime.strptime(stamp, "%Y-%m-%d-%H%M")
    except ValueError:
        return None
    try:
        tz = ZoneInfo(tz_name) if tz_name else ZoneInfo("America/Los_Angeles")
    except Exception:
        tz = ZoneInfo("America/Los_Angeles")
    return dt.replace(tzinfo=tz)


def _format_local_dt(dt: datetime | None, tz_name: str | None = None) -> str:
    if dt is None:
        return "N/A"
    try:
        tz = ZoneInfo(tz_name) if tz_name else ZoneInfo("America/Los_Angeles")
    except Exception:
        tz = ZoneInfo("America/Los_Angeles")
    local = dt.astimezone(tz)
    suffix = local.tzname() or tz.key
    return f"{local.strftime('%Y-%m-%d %H:%M')} {suffix}"


def _format_duration(start_dt: datetime | None, end_dt: datetime | None) -> str:
    if start_dt is None or end_dt is None:
        return "--"
    delta = end_dt - start_dt
    total_seconds = int(delta.total_seconds())
    if total_seconds <= 0:
        return "--"
    minutes = total_seconds // 60
    hours = minutes // 60
    minutes = minutes % 60
    if hours:
        return f"{hours}h{minutes:02d}m"
    return f"{minutes}m"


def _resume_id_from_jsonl(path: Path, tool: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if tool == "codex":
                    payload = obj.get("payload") if isinstance(obj, dict) else None
                    if isinstance(payload, dict) and payload.get("id"):
                        return str(payload.get("id"))
                else:
                    session_id = obj.get("sessionId") if isinstance(obj, dict) else None
                    if session_id:
                        return str(session_id)
                break
    except OSError:
        return None
    return None


def _session_dir_session_id(session_dir: Path) -> str | None:
    match_path = session_dir / "source_match.json"
    if not match_path.exists():
        return None
    try:
        data = json.loads(match_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    best = data.get("best") if isinstance(data, dict) else None
    session_id = best.get("session_id") if isinstance(best, dict) else None
    return session_id if isinstance(session_id, str) and session_id else None


def _session_dir_resume_id(session_dir: Path, tool: str) -> str | None:
    resume_id = _session_dir_session_id(session_dir)
    if resume_id:
        return resume_id
    export_runs_path = session_dir / "export_runs.jsonl"
    last_run = _read_last_jsonl_object(export_runs_path) if export_runs_path.exists() else None
    candidate_path = None
    if isinstance(last_run, dict):
        for key in ("copied_jsonl", "source_path"):
            value = last_run.get(key)
            if isinstance(value, str) and value:
                candidate_path = Path(value).expanduser()
                break
    if candidate_path is None or not candidate_path.exists():
        candidate_path = _choose_copied_jsonl_for_session_dir(session_dir)
    if candidate_path and candidate_path.exists():
        return _resume_id_from_jsonl(candidate_path, tool)
    return None


_INDEX_COUNTS_RE = re.compile(
    r"(\d+)\s+prompts\s+·\s+(\d+)\s+messages\s+·\s+(\d+)\s+tool calls\s+·\s+(\d+)\s+commits\s+·\s+(\d+)\s+pages",
    flags=re.IGNORECASE,
)


def _extract_counts_from_index(index_path: Path) -> dict:
    if not index_path.exists():
        return {}
    try:
        text = index_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {}
    match = _INDEX_COUNTS_RE.search(text)
    if not match:
        return {}
    try:
        prompts = int(match.group(1))
        messages = int(match.group(2))
        tool_calls = int(match.group(3))
        commits = int(match.group(4))
        pages = int(match.group(5))
    except ValueError:
        return {}
    return {
        "prompts": prompts,
        "messages": messages,
        "tool_calls": tool_calls,
        "commits": commits,
        "pages": pages,
    }


def _collect_repo_sessions(
    *,
    base_dir: Path,
    tool: str,
    limit: int | None = None,
    tz_name: str | None = None,
) -> list[dict]:
    sessions: list[dict] = []
    if not base_dir.exists():
        return sessions
    for session_dir in sorted([p for p in base_dir.iterdir() if p.is_dir()]):
        label = _derive_label_from_session_dir(session_dir) or session_dir.name
        export_runs_path = session_dir / "export_runs.jsonl"
        last_run = _read_last_jsonl_object(export_runs_path) if export_runs_path.exists() else None
        start_dt = _parse_iso8601(last_run.get("start")) if isinstance(last_run, dict) else None
        end_dt = _parse_iso8601(last_run.get("end")) if isinstance(last_run, dict) else None
        if start_dt is None:
            start_dt = _parse_ctx_stamp_from_dir(session_dir, tz_name)
        resume_id = _session_dir_resume_id(session_dir, tool)
        index_counts = _extract_counts_from_index(session_dir / "index.html")
        pages = index_counts.get("pages") or len(list(session_dir.glob("page-*.html")))
        sessions.append(
            {
                "session_dir": session_dir,
                "label": label,
                "start_dt": start_dt,
                "end_dt": end_dt,
                "duration": _format_duration(start_dt, end_dt),
                "resume_id": resume_id,
                "pages": pages,
                "prompts": index_counts.get("prompts"),
                "messages": index_counts.get("messages"),
                "tool_calls": index_counts.get("tool_calls"),
                "commits": index_counts.get("commits"),
            }
        )
    sessions.sort(key=lambda s: s["start_dt"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    if limit is not None:
        sessions = sessions[:limit]
    return sessions


def _read_jsonl_objects(path: Path) -> list[dict]:
    objs: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    objs.append(obj)
    except OSError:
        return objs
    return objs


def _choose_copied_jsonl_for_session_dir(session_dir: Path) -> Path | None:
    # Prefer the copied native JSONL (rollout-*.jsonl or <uuid>.jsonl) over legacy events.jsonl.
    candidates = [p for p in session_dir.glob("*.jsonl") if p.is_file()]
    if not candidates:
        return None
    preferred = []
    for p in candidates:
        if p.name == "events.jsonl":
            continue
        if p.name.startswith("rollout-"):
            preferred.append(p)
            continue
        if re.fullmatch(r"[0-9a-f\\-]{36}\\.jsonl", p.name, flags=re.IGNORECASE):
            preferred.append(p)
            continue
        preferred.append(p)
    # Choose largest file to bias toward the real transcript.
    preferred.sort(key=lambda p: p.stat().st_size if p.exists() else 0, reverse=True)
    return preferred[0] if preferred else None


_CODEX_RESUME_ID_RE = re.compile(
    r"\bcodex\s+resume\s+([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
    flags=re.IGNORECASE,
)


def _read_legacy_ctx_messages_json(session_dir: Path) -> dict | None:
    """Read legacy CTX `messages.json` (PTY transcription) if present."""
    path = session_dir / "messages.json"
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _read_legacy_ctx_events_first(session_dir: Path) -> dict | None:
    path = session_dir / "events.jsonl"
    if not path.exists():
        return None
    try:
        obj = _peek_first_jsonl_object(path)
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _legacy_ctx_first_user_input_from_events(session_dir: Path) -> str | None:
    path = session_dir / "events.jsonl"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                if obj.get("type") != "user_input":
                    continue
                txt = obj.get("line")
                if isinstance(txt, str) and txt.strip():
                    return txt.strip()
    except OSError:
        return None
    return None


def _legacy_ctx_first_user_input_from_transcript_md(session_dir: Path) -> str | None:
    path = session_dir / "transcript.md"
    if not path.exists():
        return None
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None

    start_idx: int | None = None
    for i, line in enumerate(lines):
        if line.strip() == "## You":
            start_idx = i + 1
            break
    if start_idx is None:
        return None

    buf: list[str] = []
    for line in lines[start_idx:]:
        if line.startswith("## ") and line.strip() != "## You":
            break
        buf.append(line)
    text = "\n".join(buf).strip()
    return text or None


def _legacy_ctx_started_from_transcript_md(session_dir: Path) -> str | None:
    path = session_dir / "transcript.md"
    if not path.exists():
        return None
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("- Started:"):
                started = line.split(":", 1)[1].strip()
                return started or None
    except OSError:
        return None
    return None


def _normalize_search_text(text: str) -> str:
    return " ".join((text or "").split()).strip()


def _prompt_search_needles(prompt_text: str) -> list[str]:
    prompt_text = prompt_text or ""
    needles: list[str] = []
    for m in re.findall(r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.md", prompt_text):
        if 10 <= len(m) <= 240:
            needles.append(m)
    normalized = _normalize_search_text(prompt_text)
    if normalized:
        needles.append(normalized[:160])
    out: list[str] = []
    seen = set()
    for n in needles:
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _find_codex_rollout_by_prompt(
    *,
    prompt_text: str,
    start_dt: datetime,
    cwd: str | None,
    max_candidates: int = 200,
) -> Path | None:
    base = _user_codex_sessions_dir()
    if not base.exists():
        return None

    needles = _prompt_search_needles(prompt_text)
    if not needles:
        return None

    candidates: list[Path] = []
    for d in _candidate_codex_day_dirs(base, start_dt, start_dt):
        if not d.exists():
            continue
        candidates.extend([p for p in d.glob("rollout-*.jsonl") if p.is_file()])
        if len(candidates) >= max_candidates:
            break

    if not candidates:
        try:
            for p in base.rglob("rollout-*.jsonl"):
                if p.is_file():
                    candidates.append(p)
                if len(candidates) >= max_candidates:
                    break
        except Exception:
            return None

    best: Path | None = None
    best_score: float | None = None
    for path in candidates:
        sess_start, _, sess_cwd, _ = _codex_rollout_session_times(path)
        if cwd and sess_cwd and not _same_path(sess_cwd, cwd):
            continue

        matched = False
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    for needle in needles:
                        if needle and needle in line:
                            matched = True
                            break
                    if matched:
                        break
        except OSError:
            continue

        if not matched:
            continue

        score = abs(((sess_start or start_dt) - start_dt).total_seconds())
        if best_score is None or score < best_score:
            best_score = score
            best = path

    return best


def _extract_codex_resume_id_from_legacy_messages(messages_obj: dict) -> str | None:
    messages = messages_obj.get("messages")
    if not isinstance(messages, list):
        return None

    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        text = msg.get("text")
        if not isinstance(text, str) or not text:
            continue
        m = _CODEX_RESUME_ID_RE.search(text)
        if m:
            return m.group(1)
    return None


def _legacy_ctx_metadata(session_dir: Path) -> dict | None:
    """Best-effort metadata for legacy PTY session directories.

    This is only used to locate/copy the underlying native JSONL so changelog
    generation can run. We never overwrite any existing files in the session dir.
    """
    messages_obj = _read_legacy_ctx_messages_json(session_dir)
    events_first = _read_legacy_ctx_events_first(session_dir)
    if messages_obj is None or events_first is None:
        return None

    started = messages_obj.get("started")
    ended = messages_obj.get("ended")
    label = messages_obj.get("label")
    tool = messages_obj.get("tool")
    project_root = messages_obj.get("project_root")
    cwd = events_first.get("cwd") or messages_obj.get("cwd")

    # Fallback timestamps from events.jsonl if messages.json is missing them.
    if not started or not ended:
        events_path = session_dir / "events.jsonl"
        last = _read_last_jsonl_object(events_path)
        started = started or (events_first.get("ts") if isinstance(events_first, dict) else None)
        ended = ended or (last.get("ts") if isinstance(last, dict) else None)

    codex_resume_id = _extract_codex_resume_id_from_legacy_messages(messages_obj)

    meta = {
        "tool": tool,
        "label": label,
        "project_root": project_root,
        "cwd": cwd,
        "start": started,
        "end": ended,
        "codex_resume_id": codex_resume_id,
    }
    return meta


def _user_codex_sessions_dir() -> Path:
    codex_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")).expanduser()
    return codex_home / "sessions"


def _candidate_codex_day_dirs(sessions_base: Path, start_dt: datetime, end_dt: datetime) -> list[Path]:
    candidate_dirs = set()
    for dt in (start_dt, end_dt):
        local = dt.astimezone()
        for offset in (-1, 0, 1):
            d = local.date() + timedelta(days=offset)
            candidate_dirs.add(sessions_base / f"{d.year:04d}" / f"{d.month:02d}" / f"{d.day:02d}")
    return sorted(candidate_dirs)


def _find_codex_rollout_by_resume_id(
    *,
    resume_id: str,
    start_dt: datetime,
    end_dt: datetime,
    cwd: str | None,
) -> Path | None:
    base = _user_codex_sessions_dir()
    if not base.exists() or not resume_id:
        return None

    pattern = f"rollout-*{resume_id}*.jsonl"
    candidates: list[Path] = []

    for d in _candidate_codex_day_dirs(base, start_dt, end_dt):
        if not d.exists():
            continue
        candidates.extend([p for p in d.glob(pattern) if p.is_file()])

    # Fallback: some installations may store rollouts outside YYYY/MM/DD.
    if not candidates:
        try:
            for p in base.rglob(pattern):
                if p.is_file():
                    candidates.append(p)
                if len(candidates) >= 25:
                    break
        except Exception:
            return None

    if not candidates:
        return None

    best_path: Path | None = None
    best_score: float | None = None
    for path in candidates:
        try:
            stat = path.stat()
        except OSError:
            continue
        mtime_dt = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        sess_start, sess_end, sess_cwd, _ = _codex_rollout_session_times(path)
        if cwd and sess_cwd and not _same_path(sess_cwd, cwd):
            continue
        sess_start = _clamp_dt(sess_start, mtime_dt)
        sess_end = _clamp_dt(sess_end, mtime_dt)
        score = abs((sess_start - start_dt).total_seconds()) + abs((sess_end - end_dt).total_seconds())
        if best_score is None or score < best_score:
            best_score = score
            best_path = path

    return best_path or candidates[0]


def _maybe_copy_native_jsonl_into_legacy_session_dir(
    *,
    tool: str,
    session_dir: Path,
    start: str | None,
    end: str | None,
    cwd: str | None,
    codex_resume_id: str | None,
) -> Path | None:
    """If this is a legacy PTY session dir, try to copy the native JSONL in place.

    Never overwrites existing files.
    """
    existing = _choose_copied_jsonl_for_session_dir(session_dir)
    if existing and existing.exists():
        return existing

    if tool != "codex":
        return None

    # Fill missing metadata from legacy artifacts when possible.
    events_first = _read_legacy_ctx_events_first(session_dir)
    if not cwd and isinstance(events_first, dict):
        ev_cwd = events_first.get("cwd")
        if isinstance(ev_cwd, str) and ev_cwd.strip():
            cwd = ev_cwd.strip()
    if not start and isinstance(events_first, dict):
        ev_start = events_first.get("ts")
        if isinstance(ev_start, str) and ev_start.strip():
            start = ev_start.strip()
    if not end:
        last = _read_last_jsonl_object(session_dir / "events.jsonl")
        ev_end = last.get("ts") if isinstance(last, dict) else None
        if isinstance(ev_end, str) and ev_end.strip():
            end = ev_end.strip()
    if not start:
        start = _legacy_ctx_started_from_transcript_md(session_dir)

    start_dt = _parse_iso8601(start) if start else None
    end_dt = _parse_iso8601(end) if end else None
    if start_dt is None:
        return None
    end_dt = end_dt or start_dt

    src: Path | None = None
    if codex_resume_id:
        src = _find_codex_rollout_by_resume_id(
            resume_id=codex_resume_id,
            start_dt=start_dt,
            end_dt=end_dt,
            cwd=cwd,
        )

    if src is None and cwd:
        try:
            match = _find_best_codex_rollout(cwd=cwd, start_dt=start_dt, end_dt=end_dt)
            src = Path(match["best"]["path"])
        except Exception:
            src = None

    if src is None or not src.exists():
        # Fallback: locate rollout by searching for the first user prompt in the transcript.
        prompt_text = _legacy_ctx_first_user_input_from_events(
            session_dir
        ) or _legacy_ctx_first_user_input_from_transcript_md(session_dir)
        if prompt_text:
            src = _find_codex_rollout_by_prompt(prompt_text=prompt_text, start_dt=start_dt, cwd=cwd)

    if src is None or not src.exists():
        return None

    dest = session_dir / src.name
    if dest.exists():
        return dest

    try:
        shutil.copy2(src, dest)
    except Exception:
        return None

    return dest


def _git_toplevel(cwd: Path) -> Path | None:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(cwd),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return Path(out).resolve() if out else None
    except Exception:
        return None


def _read_last_jsonl_object(filepath: Path, *, max_bytes: int = 256 * 1024):
    """Read and parse the last JSON object from a JSONL file."""
    try:
        with open(filepath, "rb") as f:
            f.seek(0, os.SEEK_END)
            end = f.tell()
            read_size = min(max_bytes, end)
            f.seek(end - read_size)
            chunk = f.read(read_size)
    except OSError:
        return None

    try:
        text = chunk.decode("utf-8", "replace")
    except Exception:
        return None

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for ln in reversed(lines):
        try:
            return json.loads(ln)
        except json.JSONDecodeError:
            continue
    return None


def _same_path(a: str, b: str) -> bool:
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except Exception:
        return a == b


def _codex_rollout_session_times(filepath: Path):
    """Return (start_dt, end_dt, cwd, session_id) for a Codex rollout JSONL."""
    first = _peek_first_jsonl_object(filepath)
    last = _read_last_jsonl_object(filepath)

    if not isinstance(first, dict) or first.get("type") != "session_meta":
        return None, None, None, None

    payload = first.get("payload") if isinstance(first.get("payload"), dict) else {}
    start_dt = _parse_iso8601((payload or {}).get("timestamp") or first.get("timestamp"))
    end_dt = _parse_iso8601(last.get("timestamp")) if isinstance(last, dict) else None
    cwd = (payload or {}).get("cwd")
    session_id = (payload or {}).get("id")
    return start_dt, end_dt, cwd, session_id


def _claude_session_times(filepath: Path):
    """Return (start_dt, end_dt, cwd, session_id) for a Claude JSONL session."""

    def _extract_timestamp(obj: dict):
        if not isinstance(obj, dict):
            return None

        ts = obj.get("timestamp")
        if isinstance(ts, str):
            dt = _parse_iso8601(ts)
            if dt is not None:
                return dt

        snapshot = obj.get("snapshot")
        if isinstance(snapshot, dict):
            snap_ts = snapshot.get("timestamp")
            if isinstance(snap_ts, str):
                dt = _parse_iso8601(snap_ts)
                if dt is not None:
                    return dt

        return None

    start_dt = None
    cwd = None
    session_id = None

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if start_dt is None:
                    start_dt = _extract_timestamp(obj)

                if cwd is None:
                    candidate_cwd = obj.get("cwd")
                    if isinstance(candidate_cwd, str) and candidate_cwd:
                        cwd = candidate_cwd

                if session_id is None:
                    candidate_session_id = obj.get("sessionId")
                    if isinstance(candidate_session_id, str) and candidate_session_id:
                        session_id = candidate_session_id

                if start_dt is not None and cwd is not None and session_id is not None:
                    break
    except OSError:
        return None, None, None, None

    last = _read_last_jsonl_object(filepath)
    end_dt = _extract_timestamp(last) if isinstance(last, dict) else None
    if isinstance(last, dict):
        if cwd is None:
            candidate_cwd = last.get("cwd")
            if isinstance(candidate_cwd, str) and candidate_cwd:
                cwd = candidate_cwd
        if session_id is None:
            candidate_session_id = last.get("sessionId")
            if isinstance(candidate_session_id, str) and candidate_session_id:
                session_id = candidate_session_id

    if start_dt and end_dt and start_dt > end_dt:
        start_dt = end_dt

    return start_dt, end_dt, cwd, session_id


def _clamp_dt(value, fallback):
    return value if value is not None else fallback


def find_best_source_file(*, tool: str, cwd: str, project_root: str, start: str, end: str):
    """Find the best matching native session log file for a ctx.sh run."""
    start_dt = _parse_iso8601(start)
    end_dt = _parse_iso8601(end)
    if start_dt is None or end_dt is None:
        raise click.ClickException("Invalid --start/--end timestamp (expected ISO 8601)")

    tool = (tool or "").lower()
    if tool not in ("codex", "claude"):
        raise click.ClickException("--tool must be one of: codex, claude")

    if tool == "codex":
        return _find_best_codex_rollout(cwd=cwd, start_dt=start_dt, end_dt=end_dt)
    return _find_best_claude_session(cwd=cwd, project_root=project_root, start_dt=start_dt, end_dt=end_dt)


def _find_best_codex_rollout(*, cwd: str, start_dt: datetime, end_dt: datetime):
    base = _user_codex_sessions_dir()
    if not base.exists():
        raise click.ClickException(f"Codex sessions directory not found: {base}")

    window_start = start_dt - timedelta(minutes=15)
    window_end = end_dt + timedelta(minutes=15)

    # Restrict search to a few day-folders around the session, based on local time.
    candidate_dirs = set()
    for dt in (start_dt, end_dt):
        local = dt.astimezone()
        for offset in (-1, 0, 1):
            d = local.date() + timedelta(days=offset)
            candidate_dirs.add(base / f"{d.year:04d}" / f"{d.month:02d}" / f"{d.day:02d}")

    candidates = []
    for d in sorted(candidate_dirs):
        if not d.exists():
            continue
        for path in d.glob("rollout-*.jsonl"):
            try:
                stat = path.stat()
            except OSError:
                continue
            mtime_dt = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            if mtime_dt < window_start or mtime_dt > window_end:
                continue

            sess_start, sess_end, sess_cwd, sess_id = _codex_rollout_session_times(path)
            if sess_cwd and not _same_path(sess_cwd, cwd):
                continue

            sess_start = _clamp_dt(sess_start, mtime_dt)
            sess_end = _clamp_dt(sess_end, mtime_dt)

            score = abs((sess_start - start_dt).total_seconds()) + abs((sess_end - end_dt).total_seconds())
            candidates.append(
                {
                    "path": str(path),
                    "score": score,
                    "session_id": sess_id,
                    "cwd": sess_cwd,
                    "start": sess_start.isoformat(),
                    "end": sess_end.isoformat(),
                    "mtime": mtime_dt.isoformat(),
                    "size_bytes": stat.st_size,
                }
            )

    if not candidates:
        raise click.ClickException("No matching Codex rollout files found")

    candidates.sort(key=lambda c: c["score"])
    return {"best": candidates[0], "candidates": candidates[:25]}


def _encode_claude_project_folder(path: str) -> str:
    path = os.path.abspath(path)
    path = path.strip(os.sep)
    return "-" + path.replace(os.sep, "-")


def _find_best_claude_session(*, cwd: str, project_root: str, start_dt: datetime, end_dt: datetime):
    base = Path.home() / ".claude" / "projects"
    if not base.exists():
        raise click.ClickException(f"Claude projects directory not found: {base}")

    window_start = start_dt - timedelta(minutes=15)
    window_end = end_dt + timedelta(minutes=15)

    # Prefer the encoded git project root folder, with fallback to cwd.
    candidate_dirs = []
    for p in [project_root, cwd]:
        if not p:
            continue
        d = base / _encode_claude_project_folder(p)
        if d.exists() and d not in candidate_dirs:
            candidate_dirs.append(d)

    # Fallback: scan all project folders if we couldn't resolve a directory.
    if not candidate_dirs:
        candidate_dirs = [p for p in base.iterdir() if p.is_dir()]

    candidates = []
    for d in candidate_dirs:
        for path in d.glob("*.jsonl"):
            if path.name.startswith("agent-"):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            mtime_dt = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            if mtime_dt < window_start or mtime_dt > window_end:
                continue

            sess_start, sess_end, sess_cwd, sess_id = _claude_session_times(path)
            if sess_cwd and not _same_path(sess_cwd, cwd) and project_root and not _same_path(sess_cwd, project_root):
                continue

            sess_start = _clamp_dt(sess_start, mtime_dt)
            sess_end = _clamp_dt(sess_end, mtime_dt)

            # Claude session logs may include earlier history when resuming. Prefer end-alignment with
            # the export window, and only penalize start times that begin after the window start.
            score = abs((sess_end - end_dt).total_seconds()) + max(0.0, (sess_start - start_dt).total_seconds())
            candidates.append(
                {
                    "path": str(path),
                    "score": score,
                    "session_id": sess_id,
                    "cwd": sess_cwd,
                    "start": sess_start.isoformat(),
                    "end": sess_end.isoformat(),
                    "mtime": mtime_dt.isoformat(),
                    "size_bytes": stat.st_size,
                }
            )

    if not candidates:
        raise click.ClickException("No matching Claude session files found")

    candidates.sort(key=lambda c: c["score"])
    return {"best": candidates[0], "candidates": candidates[:25]}


class CredentialsError(Exception):
    """Raised when credentials cannot be obtained."""

    pass


def get_access_token_from_keychain():
    """Get access token from macOS keychain.

    Returns the access token or None if not found.
    Raises CredentialsError with helpful message on failure.
    """
    if platform.system() != "Darwin":
        return None

    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-a",
                os.environ.get("USER", ""),
                "-s",
                "Claude Code-credentials",
                "-w",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None

        # Parse the JSON to get the access token
        creds = json.loads(result.stdout.strip())
        return creds.get("claudeAiOauth", {}).get("accessToken")
    except (json.JSONDecodeError, subprocess.SubprocessError):
        return None


def get_org_uuid_from_config():
    """Get organization UUID from ~/.claude.json.

    Returns the organization UUID or None if not found.
    """
    config_path = Path.home() / ".claude.json"
    if not config_path.exists():
        return None

    try:
        with open(config_path) as f:
            config = json.load(f)
        return config.get("oauthAccount", {}).get("organizationUuid")
    except (json.JSONDecodeError, IOError):
        return None


def resolve_credentials(token, org_uuid):
    """Resolve token and org_uuid from arguments or auto-detect.

    Returns (token, org_uuid) tuple.
    Raises click.ClickException if credentials cannot be resolved.
    """
    if token is None:
        token = get_access_token_from_keychain()
        if token is None:
            if platform.system() == "Darwin":
                raise click.ClickException(
                    "Could not retrieve access token from macOS keychain. "
                    "Make sure you are logged into Claude Code, or provide --token."
                )
            raise click.ClickException("On non-macOS platforms, you must provide --token with your access token.")

    if org_uuid is None:
        org_uuid = get_org_uuid_from_config()
        if org_uuid is None:
            raise click.ClickException(
                "Could not find organization UUID in ~/.claude.json. Provide --org-uuid with your organization UUID."
            )

    return token, org_uuid


def format_session_for_display(session_data):
    """Format a session for display in the list or picker."""
    session_id = session_data.get("id", "unknown")
    title = session_data.get("title", "Untitled")
    created_at = session_data.get("created_at", "")
    if len(title) > 60:
        title = title[:57] + "..."
    return f"{session_id}  {created_at[:19] if created_at else 'N/A':19}  {title}"


def get_api_headers(token, org_uuid):
    """Build API request headers."""
    return {
        "Authorization": f"Bearer {token}",
        "anthropic-version": ANTHROPIC_VERSION,
        "Content-Type": "application/json",
        "x-organization-uuid": org_uuid,
    }


def fetch_sessions(token, org_uuid):
    """Fetch list of sessions from the API.

    Returns the sessions data as a dict.
    Raises httpx.HTTPError on network/API errors.
    """
    headers = get_api_headers(token, org_uuid)
    response = httpx.get(f"{API_BASE_URL}/sessions", headers=headers, timeout=30.0)
    response.raise_for_status()
    return response.json()


def fetch_session(token, org_uuid, session_id):
    """Fetch a specific session from the API.

    Returns the session data as a dict.
    Raises httpx.HTTPError on network/API errors.
    """
    headers = get_api_headers(token, org_uuid)
    response = httpx.get(
        f"{API_BASE_URL}/session_ingress/session/{session_id}",
        headers=headers,
        timeout=60.0,
    )
    response.raise_for_status()
    return response.json()


def _github_repo_from_repository_url(repository_url: str) -> str | None:
    if not isinstance(repository_url, str):
        return None
    url = repository_url.strip()
    if not url:
        return None
    m = GITHUB_REPO_URL_PATTERN.search(url)
    if m:
        repo = m.group("repo")
        if repo.endswith(".git"):
            repo = repo[:-4]
        return repo
    return None


def _detect_github_repo_from_meta(meta: dict | None) -> str | None:
    if not isinstance(meta, dict):
        return None
    git = meta.get("git")
    if isinstance(git, dict):
        repo_url = git.get("repository_url") or git.get("repositoryUrl") or git.get("repo_url") or git.get("repoUrl")
    else:
        repo_url = meta.get("repository_url")
    if isinstance(repo_url, str):
        return _github_repo_from_repository_url(repo_url)
    return None


def detect_github_repo(loglines):
    """
    Detect GitHub repo from git push output in tool results.

    Looks for patterns like:
    - github.com/owner/repo/pull/new/branch (from git push messages)

    Returns the first detected repo (owner/name) or None.
    """
    for entry in loglines:
        message = entry.get("message", {})
        content = message.get("content", [])
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_result":
                result_content = block.get("content", "")
                if isinstance(result_content, str):
                    match = GITHUB_REPO_PATTERN.search(result_content)
                    if match:
                        return match.group(1)
    return None


def format_json(obj):
    try:
        if isinstance(obj, str):
            obj = json.loads(obj)
        formatted = json.dumps(obj, indent=2, ensure_ascii=False)
        return f'<pre class="json">{html.escape(formatted)}</pre>'
    except (json.JSONDecodeError, TypeError):
        return f"<pre>{html.escape(str(obj))}</pre>"


@lru_cache(maxsize=2048)
def render_markdown_text(text):
    if not text:
        return ""
    return markdown.markdown(text, extensions=["fenced_code", "tables"])


def is_json_like(text):
    if not text or not isinstance(text, str):
        return False
    text = text.strip()
    return (text.startswith("{") and text.endswith("}")) or (text.startswith("[") and text.endswith("]"))


def render_todo_write(tool_input, tool_id):
    todos = tool_input.get("todos", [])
    if not todos:
        return ""
    return _macros.todo_list(todos, tool_id)


def render_write_tool(tool_input, tool_id):
    """Render Write tool calls with file path header and content preview."""
    file_path = tool_input.get("file_path", "Unknown file")
    content = tool_input.get("content", "")
    return _macros.write_tool(file_path, content, tool_id)


def render_edit_tool(tool_input, tool_id):
    """Render Edit tool calls with diff-like old/new display."""
    file_path = tool_input.get("file_path", "Unknown file")
    old_string = tool_input.get("old_string", "")
    new_string = tool_input.get("new_string", "")
    replace_all = tool_input.get("replace_all", False)
    return _macros.edit_tool(file_path, old_string, new_string, replace_all, tool_id)


def render_bash_tool(tool_input, tool_id):
    """Render Bash tool calls with command as plain text."""
    command = tool_input.get("command", "")
    description = tool_input.get("description", "")
    return _macros.bash_tool(command, description, tool_id)


def render_content_block(block):
    if not isinstance(block, dict):
        return f"<p>{html.escape(str(block))}</p>"
    block_type = block.get("type", "")
    if block_type == "image":
        source = block.get("source", {})
        media_type = source.get("media_type", "image/png")
        data = source.get("data", "")
        return _macros.image_block(media_type, data)
    elif block_type == "thinking":
        content_html = render_markdown_text(block.get("thinking", ""))
        return _macros.thinking(content_html)
    elif block_type == "text":
        content_html = render_markdown_text(block.get("text", ""))
        return _macros.assistant_text(content_html)
    elif block_type == "tool_use":
        tool_name = block.get("name", "Unknown tool")
        tool_input = block.get("input", {})
        tool_id = block.get("id", "")
        if tool_name == "TodoWrite":
            return render_todo_write(tool_input, tool_id)
        if tool_name == "Write":
            return render_write_tool(tool_input, tool_id)
        if tool_name == "Edit":
            return render_edit_tool(tool_input, tool_id)
        if tool_name == "Bash":
            return render_bash_tool(tool_input, tool_id)
        description = tool_input.get("description", "")
        display_input = {k: v for k, v in tool_input.items() if k != "description"}
        input_json = json.dumps(display_input, indent=2, ensure_ascii=False)
        return _macros.tool_use(tool_name, description, input_json, tool_id)
    elif block_type == "tool_result":
        content = block.get("content", "")
        is_error = block.get("is_error", False)

        # Check for git commits and render with styled cards
        if isinstance(content, str):
            commits_found = list(COMMIT_PATTERN.finditer(content))
            if commits_found:
                # Build commit cards + remaining content
                parts = []
                last_end = 0
                for match in commits_found:
                    # Add any content before this commit
                    before = content[last_end : match.start()].strip()
                    if before:
                        parts.append(f"<pre>{html.escape(before)}</pre>")

                    commit_hash = match.group(1)
                    commit_msg = match.group(2)
                    parts.append(_macros.commit_card(commit_hash, commit_msg, _github_repo))
                    last_end = match.end()

                # Add any remaining content after last commit
                after = content[last_end:].strip()
                if after:
                    parts.append(f"<pre>{html.escape(after)}</pre>")

                content_html = "".join(parts)
            else:
                content_html = f"<pre>{html.escape(content)}</pre>"
        elif isinstance(content, list) or is_json_like(content):
            content_html = format_json(content)
        else:
            content_html = format_json(content)
        return _macros.tool_result(content_html, is_error)
    else:
        return format_json(block)


def render_user_message_content(message_data):
    content = message_data.get("content", "")
    if isinstance(content, str):
        if is_json_like(content):
            return _macros.user_content(format_json(content))
        return _macros.user_content(render_markdown_text(content))
    elif isinstance(content, list):
        return "".join(render_content_block(block) for block in content)
    return f"<p>{html.escape(str(content))}</p>"


def render_assistant_message(message_data):
    content = message_data.get("content", [])
    if not isinstance(content, list):
        return f"<p>{html.escape(str(content))}</p>"
    return "".join(render_content_block(block) for block in content)


def _load_message_json(message_json: str | None) -> dict | None:
    if not message_json:
        return None
    try:
        return json.loads(message_json)
    except json.JSONDecodeError:
        return None


def _message_role_class(log_type: str, message_data: dict | None) -> str:
    if log_type == "user":
        if message_data and is_tool_result_message(message_data):
            return "tool-reply"
        return "user"
    if log_type == "assistant":
        return "assistant"
    return "message"


def _message_plain_text(message_data: dict | None) -> str:
    if not isinstance(message_data, dict):
        return ""
    content = message_data.get("content", "")
    texts: list[str] = []
    if isinstance(content, str):
        if content.strip():
            texts.append(content.strip())
    elif isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                txt = block.get("text", "")
                if isinstance(txt, str) and txt.strip():
                    texts.append(txt.strip())
            elif btype == "thinking":
                thinking = block.get("thinking", "")
                if isinstance(thinking, str) and thinking.strip():
                    texts.append(thinking.strip())
            elif btype == "tool_use":
                tool_name = block.get("name", "")
                if tool_name:
                    texts.append(f"[tool_use:{tool_name}]")
                tool_input = block.get("input")
                if tool_input is not None:
                    try:
                        texts.append(json.dumps(tool_input, ensure_ascii=False))
                    except TypeError:
                        texts.append(str(tool_input))
            elif btype == "tool_result":
                result = block.get("content", "")
                if isinstance(result, (dict, list)):
                    texts.append(json.dumps(result, ensure_ascii=False))
                elif isinstance(result, str):
                    if result.strip():
                        texts.append(result.strip())
                else:
                    texts.append(str(result))
    text = "\n".join(t for t in texts if t)
    if not text:
        return ""
    return _truncate_text_middle(text, SEARCH_INDEX_TEXT_MAX_CHARS)


def _build_search_index(*, conversations: list[dict], total_pages: int) -> dict:
    items: list[dict] = []
    for i, conv in enumerate(conversations):
        page_num = (i // PROMPTS_PER_PAGE) + 1
        for log_type, message_json, timestamp in conv.get("messages", []):
            message_data = _load_message_json(message_json)
            text = _message_plain_text(message_data)
            if not text:
                continue
            msg_id = make_msg_id(timestamp)
            role_class = _message_role_class(log_type, message_data)
            items.append(
                {
                    "page": page_num,
                    "msg_id": msg_id,
                    "timestamp": timestamp,
                    "role": role_class,
                    "text": text,
                }
            )
    return {
        "schema_version": SEARCH_INDEX_SCHEMA_VERSION,
        "total_pages": total_pages,
        "items": items,
    }


def _json_for_script(obj: dict) -> str:
    raw = json.dumps(obj, ensure_ascii=False)
    return raw.replace("</", "<\\/")


def make_msg_id(timestamp):
    return f"msg-{timestamp.replace(':', '-').replace('.', '-')}"


def analyze_conversation(messages):
    """Analyze messages in a conversation to extract stats and long texts."""
    tool_counts = {}  # tool_name -> count
    long_texts = []
    commits = []  # list of (hash, message, timestamp)

    for log_type, message_json, timestamp in messages:
        if not message_json:
            continue
        try:
            message_data = json.loads(message_json)
        except json.JSONDecodeError:
            continue

        content = message_data.get("content", [])
        if not isinstance(content, list):
            continue

        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type", "")

            if block_type == "tool_use":
                tool_name = block.get("name", "Unknown")
                tool_counts[tool_name] = tool_counts.get(tool_name, 0) + 1
            elif block_type == "tool_result":
                # Check for git commit output
                result_content = block.get("content", "")
                if isinstance(result_content, str):
                    for match in COMMIT_PATTERN.finditer(result_content):
                        commits.append((match.group(1), match.group(2), timestamp))
            elif block_type == "text":
                text = block.get("text", "")
                if len(text) >= LONG_TEXT_THRESHOLD:
                    long_texts.append(text)

    return {
        "tool_counts": tool_counts,
        "long_texts": long_texts,
        "commits": commits,
    }


def format_tool_stats(tool_counts):
    """Format tool counts into a concise summary string."""
    if not tool_counts:
        return ""

    # Abbreviate common tool names
    abbrev = {
        "Bash": "bash",
        "Read": "read",
        "Write": "write",
        "Edit": "edit",
        "Glob": "glob",
        "Grep": "grep",
        "Task": "task",
        "TodoWrite": "todo",
        "WebFetch": "fetch",
        "WebSearch": "search",
    }

    parts = []
    for name, count in sorted(tool_counts.items(), key=lambda x: -x[1]):
        short_name = abbrev.get(name, name.lower())
        parts.append(f"{count} {short_name}")

    return " · ".join(parts)


def is_tool_result_message(message_data):
    """Check if a message contains only tool_result blocks."""
    content = message_data.get("content", [])
    if not isinstance(content, list):
        return False
    if not content:
        return False
    return all(isinstance(block, dict) and block.get("type") == "tool_result" for block in content)


def render_message(log_type, message_json, timestamp):
    if not message_json:
        return ""
    try:
        message_data = json.loads(message_json)
    except json.JSONDecodeError:
        return ""
    if log_type == "user":
        content_html = render_user_message_content(message_data)
        # Check if this is a tool result message
        if is_tool_result_message(message_data):
            role_class, role_label = "tool-reply", "Tool reply"
        else:
            role_class, role_label = "user", "User"
    elif log_type == "assistant":
        content_html = render_assistant_message(message_data)
        role_class, role_label = "assistant", "Assistant"
    else:
        return ""
    if not content_html.strip():
        return ""
    msg_id = make_msg_id(timestamp)
    return _macros.message(role_class, role_label, msg_id, timestamp, content_html)


CSS = """
:root { --bg-color: #f5f5f5; --card-bg: #ffffff; --user-bg: #e3f2fd; --user-border: #1976d2; --assistant-bg: #f5f5f5; --assistant-border: #9e9e9e; --thinking-bg: #fff8e1; --thinking-border: #ffc107; --thinking-text: #666; --tool-bg: #f3e5f5; --tool-border: #9c27b0; --tool-result-bg: #e8f5e9; --tool-error-bg: #ffebee; --text-color: #212121; --text-muted: #757575; --code-bg: #263238; --code-text: #aed581; }
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: var(--bg-color); color: var(--text-color); margin: 0; padding: 16px; line-height: 1.6; }
.container { max-width: 800px; margin: 0 auto; }
h1 { font-size: 1.5rem; margin-bottom: 24px; padding-bottom: 8px; border-bottom: 2px solid var(--user-border); }
.header-row { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; border-bottom: 2px solid var(--user-border); padding-bottom: 8px; margin-bottom: 24px; }
.header-row h1 { border-bottom: none; padding-bottom: 0; margin-bottom: 0; flex: 1; min-width: 200px; }
.message { margin-bottom: 16px; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
.message.user { background: var(--user-bg); border-left: 4px solid var(--user-border); }
.message.assistant { background: var(--card-bg); border-left: 4px solid var(--assistant-border); }
.message.tool-reply { background: #fff8e1; border-left: 4px solid #ff9800; }
.tool-reply .role-label { color: #e65100; }
.tool-reply .tool-result { background: transparent; padding: 0; margin: 0; }
.tool-reply .tool-result .truncatable.truncated::after { background: linear-gradient(to bottom, transparent, #fff8e1); }
.message-header { display: flex; justify-content: space-between; align-items: center; padding: 8px 16px; background: rgba(0,0,0,0.03); font-size: 0.85rem; }
.role-label { font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
.user .role-label { color: var(--user-border); }
time { color: var(--text-muted); font-size: 0.8rem; }
.timestamp-link { color: inherit; text-decoration: none; }
.timestamp-link:hover { text-decoration: underline; }
.message:target { animation: highlight 2s ease-out; }
@keyframes highlight { 0% { background-color: rgba(25, 118, 210, 0.2); } 100% { background-color: transparent; } }
.message-content { padding: 16px; }
.message-content p { margin: 0 0 12px 0; }
.message-content p:last-child { margin-bottom: 0; }
.thinking { background: var(--thinking-bg); border: 1px solid var(--thinking-border); border-radius: 8px; padding: 12px; margin: 12px 0; font-size: 0.9rem; color: var(--thinking-text); }
.thinking-label { font-size: 0.75rem; font-weight: 600; text-transform: uppercase; color: #f57c00; margin-bottom: 8px; }
.thinking p { margin: 8px 0; }
.assistant-text { margin: 8px 0; }
.tool-use { background: var(--tool-bg); border: 1px solid var(--tool-border); border-radius: 8px; padding: 12px; margin: 12px 0; }
.tool-header { font-weight: 600; color: var(--tool-border); margin-bottom: 8px; display: flex; align-items: center; gap: 8px; }
.tool-icon { font-size: 1.1rem; }
.tool-description { font-size: 0.9rem; color: var(--text-muted); margin-bottom: 8px; font-style: italic; }
.tool-result { background: var(--tool-result-bg); border-radius: 8px; padding: 12px; margin: 12px 0; }
.tool-result.tool-error { background: var(--tool-error-bg); }
.file-tool { border-radius: 8px; padding: 12px; margin: 12px 0; }
.write-tool { background: linear-gradient(135deg, #e3f2fd 0%, #e8f5e9 100%); border: 1px solid #4caf50; }
.edit-tool { background: linear-gradient(135deg, #fff3e0 0%, #fce4ec 100%); border: 1px solid #ff9800; }
.file-tool-header { font-weight: 600; margin-bottom: 4px; display: flex; align-items: center; gap: 8px; font-size: 0.95rem; }
.write-header { color: #2e7d32; }
.edit-header { color: #e65100; }
.file-tool-icon { font-size: 1rem; }
.file-tool-path { font-family: monospace; background: rgba(0,0,0,0.08); padding: 2px 8px; border-radius: 4px; }
.file-tool-fullpath { font-family: monospace; font-size: 0.8rem; color: var(--text-muted); margin-bottom: 8px; word-break: break-all; }
.file-content { margin: 0; }
.edit-section { display: flex; margin: 4px 0; border-radius: 4px; overflow: hidden; }
.edit-label { padding: 8px 12px; font-weight: bold; font-family: monospace; display: flex; align-items: flex-start; }
.edit-old { background: #fce4ec; }
.edit-old .edit-label { color: #b71c1c; background: #f8bbd9; }
.edit-old .edit-content { color: #880e4f; }
.edit-new { background: #e8f5e9; }
.edit-new .edit-label { color: #1b5e20; background: #a5d6a7; }
.edit-new .edit-content { color: #1b5e20; }
.edit-content { margin: 0; flex: 1; background: transparent; font-size: 0.85rem; }
.edit-replace-all { font-size: 0.75rem; font-weight: normal; color: var(--text-muted); }
.write-tool .truncatable.truncated::after { background: linear-gradient(to bottom, transparent, #e6f4ea); }
.edit-tool .truncatable.truncated::after { background: linear-gradient(to bottom, transparent, #fff0e5); }
.todo-list { background: linear-gradient(135deg, #e8f5e9 0%, #f1f8e9 100%); border: 1px solid #81c784; border-radius: 8px; padding: 12px; margin: 12px 0; }
.todo-header { font-weight: 600; color: #2e7d32; margin-bottom: 10px; display: flex; align-items: center; gap: 8px; font-size: 0.95rem; }
.todo-items { list-style: none; margin: 0; padding: 0; }
.todo-item { display: flex; align-items: flex-start; gap: 10px; padding: 6px 0; border-bottom: 1px solid rgba(0,0,0,0.06); font-size: 0.9rem; }
.todo-item:last-child { border-bottom: none; }
.todo-icon { flex-shrink: 0; width: 20px; height: 20px; display: flex; align-items: center; justify-content: center; font-weight: bold; border-radius: 50%; }
.todo-completed .todo-icon { color: #2e7d32; background: rgba(46, 125, 50, 0.15); }
.todo-completed .todo-content { color: #558b2f; text-decoration: line-through; }
.todo-in-progress .todo-icon { color: #f57c00; background: rgba(245, 124, 0, 0.15); }
.todo-in-progress .todo-content { color: #e65100; font-weight: 500; }
.todo-pending .todo-icon { color: #757575; background: rgba(0,0,0,0.05); }
.todo-pending .todo-content { color: #616161; }
pre { background: var(--code-bg); color: var(--code-text); padding: 12px; border-radius: 6px; overflow-x: auto; font-size: 0.85rem; line-height: 1.5; margin: 8px 0; white-space: pre-wrap; word-wrap: break-word; }
pre.json { color: #e0e0e0; }
code { background: rgba(0,0,0,0.08); padding: 2px 6px; border-radius: 4px; font-size: 0.9em; }
pre code { background: none; padding: 0; }
.user-content { margin: 0; }
.truncatable { position: relative; }
.truncatable.truncated .truncatable-content { max-height: 200px; overflow: hidden; }
.truncatable.truncated::after { content: ''; position: absolute; bottom: 32px; left: 0; right: 0; height: 60px; background: linear-gradient(to bottom, transparent, var(--card-bg)); pointer-events: none; }
.message.user .truncatable.truncated::after { background: linear-gradient(to bottom, transparent, var(--user-bg)); }
.message.tool-reply .truncatable.truncated::after { background: linear-gradient(to bottom, transparent, #fff8e1); }
.tool-use .truncatable.truncated::after { background: linear-gradient(to bottom, transparent, var(--tool-bg)); }
.tool-result .truncatable.truncated::after { background: linear-gradient(to bottom, transparent, var(--tool-result-bg)); }
.expand-btn { display: none; width: 100%; padding: 8px 16px; margin-top: 4px; background: rgba(0,0,0,0.05); border: 1px solid rgba(0,0,0,0.1); border-radius: 6px; cursor: pointer; font-size: 0.85rem; color: var(--text-muted); }
.expand-btn:hover { background: rgba(0,0,0,0.1); }
.truncatable.truncated .expand-btn, .truncatable.expanded .expand-btn { display: block; }
.pagination { display: flex; justify-content: center; gap: 8px; margin: 24px 0; flex-wrap: wrap; }
.pagination a, .pagination span { padding: 5px 10px; border-radius: 6px; text-decoration: none; font-size: 0.85rem; }
.pagination a { background: var(--card-bg); color: var(--user-border); border: 1px solid var(--user-border); }
.pagination a:hover { background: var(--user-bg); }
.pagination .current { background: var(--user-border); color: white; }
.pagination .disabled { color: var(--text-muted); border: 1px solid #ddd; }
.pagination .index-link { background: var(--user-border); color: white; }
details.continuation { margin-bottom: 16px; }
details.continuation summary { cursor: pointer; padding: 12px 16px; background: var(--user-bg); border-left: 4px solid var(--user-border); border-radius: 12px; font-weight: 500; color: var(--text-muted); }
details.continuation summary:hover { background: rgba(25, 118, 210, 0.15); }
details.continuation[open] summary { border-radius: 12px 12px 0 0; margin-bottom: 0; }
.index-item { margin-bottom: 16px; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); background: var(--user-bg); border-left: 4px solid var(--user-border); }
.index-item a { display: block; text-decoration: none; color: inherit; }
.index-item a:hover { background: rgba(25, 118, 210, 0.1); }
.index-item-header { display: flex; justify-content: space-between; align-items: center; padding: 8px 16px; background: rgba(0,0,0,0.03); font-size: 0.85rem; }
.index-item-number { font-weight: 600; color: var(--user-border); }
.index-item-content { padding: 16px; }
.index-item-stats { padding: 8px 16px 12px 32px; font-size: 0.85rem; color: var(--text-muted); border-top: 1px solid rgba(0,0,0,0.06); }
.index-item-commit { margin-top: 6px; padding: 4px 8px; background: #fff3e0; border-radius: 4px; font-size: 0.85rem; color: #e65100; }
.index-item-commit code { background: rgba(0,0,0,0.08); padding: 1px 4px; border-radius: 3px; font-size: 0.8rem; margin-right: 6px; }
.commit-card { margin: 8px 0; padding: 10px 14px; background: #fff3e0; border-left: 4px solid #ff9800; border-radius: 6px; }
.commit-card a { text-decoration: none; color: #5d4037; display: block; }
.commit-card a:hover { color: #e65100; }
.commit-card-hash { font-family: monospace; color: #e65100; font-weight: 600; margin-right: 8px; }
.index-commit { margin-bottom: 12px; padding: 10px 16px; background: #fff3e0; border-left: 4px solid #ff9800; border-radius: 8px; box-shadow: 0 1px 2px rgba(0,0,0,0.05); }
.index-commit a { display: block; text-decoration: none; color: inherit; }
.index-commit a:hover { background: rgba(255, 152, 0, 0.1); margin: -10px -16px; padding: 10px 16px; border-radius: 8px; }
.index-commit-header { display: flex; justify-content: space-between; align-items: center; font-size: 0.85rem; margin-bottom: 4px; }
.index-commit-hash { font-family: monospace; color: #e65100; font-weight: 600; }
.index-commit-msg { color: #5d4037; }
.index-item-long-text { margin-top: 8px; padding: 12px; background: var(--card-bg); border-radius: 8px; border-left: 3px solid var(--assistant-border); }
.index-item-long-text .truncatable.truncated::after { background: linear-gradient(to bottom, transparent, var(--card-bg)); }
.index-item-long-text-content { color: var(--text-color); }
#search-box { display: none; align-items: center; gap: 8px; }
#search-box input { padding: 6px 12px; border: 1px solid var(--assistant-border); border-radius: 6px; font-size: 16px; width: 180px; }
#search-box button, #modal-search-btn, #modal-close-btn { background: var(--user-border); color: white; border: none; border-radius: 6px; padding: 6px 10px; cursor: pointer; display: flex; align-items: center; justify-content: center; }
#search-box button:hover, #modal-search-btn:hover { background: #1565c0; }
#modal-close-btn { background: var(--text-muted); margin-left: 8px; }
#modal-close-btn:hover { background: #616161; }
#search-modal[open] { border: none; border-radius: 12px; box-shadow: 0 4px 24px rgba(0,0,0,0.2); padding: 0; width: 90vw; max-width: 900px; height: 80vh; max-height: 80vh; display: flex; flex-direction: column; }
#search-modal::backdrop { background: rgba(0,0,0,0.5); }
.search-modal-header { display: flex; align-items: center; gap: 8px; padding: 16px; border-bottom: 1px solid var(--assistant-border); background: var(--bg-color); border-radius: 12px 12px 0 0; }
.search-modal-header input { flex: 1; padding: 8px 12px; border: 1px solid var(--assistant-border); border-radius: 6px; font-size: 16px; }
#search-status { padding: 8px 16px; font-size: 0.85rem; color: var(--text-muted); border-bottom: 1px solid rgba(0,0,0,0.06); }
#search-results { flex: 1; overflow-y: auto; padding: 16px; }
.search-result { margin-bottom: 16px; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
.search-result a { display: block; text-decoration: none; color: inherit; }
.search-result a:hover { background: rgba(25, 118, 210, 0.05); }
.search-result-page { padding: 6px 12px; background: rgba(0,0,0,0.03); font-size: 0.8rem; color: var(--text-muted); border-bottom: 1px solid rgba(0,0,0,0.06); }
.search-result-content { padding: 12px; }
.search-result mark { background: #fff59d; padding: 1px 2px; border-radius: 2px; }
@media (max-width: 600px) { body { padding: 8px; } .message, .index-item { border-radius: 8px; } .message-content, .index-item-content { padding: 12px; } pre { font-size: 0.8rem; padding: 8px; } #search-box input { width: 120px; } #search-modal[open] { width: 95vw; height: 90vh; } }
"""

JS = """
document.querySelectorAll('time[data-timestamp]').forEach(function(el) {
    const timestamp = el.getAttribute('data-timestamp');
    const date = new Date(timestamp);
    const now = new Date();
    const isToday = date.toDateString() === now.toDateString();
    const timeStr = date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
    if (isToday) { el.textContent = timeStr; }
    else { el.textContent = date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ' ' + timeStr; }
});
document.querySelectorAll('pre.json').forEach(function(el) {
    let text = el.textContent;
    text = text.replace(/"([^"]+)":/g, '<span style="color: #ce93d8">"$1"</span>:');
    text = text.replace(/: "([^"]*)"/g, ': <span style="color: #81d4fa">"$1"</span>');
    text = text.replace(/: (\\d+)/g, ': <span style="color: #ffcc80">$1</span>');
    text = text.replace(/: (true|false|null)/g, ': <span style="color: #f48fb1">$1</span>');
    el.innerHTML = text;
});
document.querySelectorAll('.truncatable').forEach(function(wrapper) {
    const content = wrapper.querySelector('.truncatable-content');
    const btn = wrapper.querySelector('.expand-btn');
    if (content.scrollHeight > 250) {
        wrapper.classList.add('truncated');
        btn.addEventListener('click', function() {
            if (wrapper.classList.contains('truncated')) { wrapper.classList.remove('truncated'); wrapper.classList.add('expanded'); btn.textContent = 'Show less'; }
            else { wrapper.classList.remove('expanded'); wrapper.classList.add('truncated'); btn.textContent = 'Show more'; }
        });
    }
});
"""

# JavaScript to fix relative URLs when served via gisthost.github.io or gistpreview.github.io
# Fixes issue #26: Pagination links broken on gisthost.github.io
GIST_PREVIEW_JS = r"""
(function() {
    var hostname = window.location.hostname;
    if (hostname !== 'gisthost.github.io' && hostname !== 'gistpreview.github.io') return;
    // URL format: https://gisthost.github.io/?GIST_ID/filename.html
    var match = window.location.search.match(/^\?([^/]+)/);
    if (!match) return;
    var gistId = match[1];

    function rewriteLinks(root) {
        (root || document).querySelectorAll('a[href]').forEach(function(link) {
            var href = link.getAttribute('href');
            // Skip already-rewritten links (issue #26 fix)
            if (href.startsWith('?')) return;
            // Skip external links and anchors
            if (href.startsWith('http') || href.startsWith('#') || href.startsWith('//')) return;
            // Handle anchor in relative URL (e.g., page-001.html#msg-123)
            var parts = href.split('#');
            var filename = parts[0];
            var anchor = parts.length > 1 ? '#' + parts[1] : '';
            link.setAttribute('href', '?' + gistId + '/' + filename + anchor);
        });
    }

    // Run immediately
    rewriteLinks();

    // Also run on DOMContentLoaded in case DOM isn't ready yet
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() { rewriteLinks(); });
    }

    // Use MutationObserver to catch dynamically added content
    // gistpreview.github.io may add content after initial load
    var observer = new MutationObserver(function(mutations) {
        mutations.forEach(function(mutation) {
            mutation.addedNodes.forEach(function(node) {
                if (node.nodeType === 1) { // Element node
                    rewriteLinks(node);
                    // Also check if the node itself is a link
                    if (node.tagName === 'A' && node.getAttribute('href')) {
                        var href = node.getAttribute('href');
                        if (!href.startsWith('?') && !href.startsWith('http') &&
                            !href.startsWith('#') && !href.startsWith('//')) {
                            var parts = href.split('#');
                            var filename = parts[0];
                            var anchor = parts.length > 1 ? '#' + parts[1] : '';
                            node.setAttribute('href', '?' + gistId + '/' + filename + anchor);
                        }
                    }
                }
            });
        });
    });

    // Start observing once body exists
    function startObserving() {
        if (document.body) {
            observer.observe(document.body, { childList: true, subtree: true });
        } else {
            setTimeout(startObserving, 10);
        }
    }
    startObserving();

    // Handle fragment navigation after dynamic content loads
    // gisthost.github.io/gistpreview.github.io loads content dynamically, so the browser's
    // native fragment navigation fails because the element doesn't exist yet
    function scrollToFragment() {
        var hash = window.location.hash;
        if (!hash) return false;
        var targetId = hash.substring(1);
        var target = document.getElementById(targetId);
        if (target) {
            target.scrollIntoView({ behavior: 'smooth', block: 'start' });
            return true;
        }
        return false;
    }

    // Try immediately in case content is already loaded
    if (!scrollToFragment()) {
        // Retry with increasing delays to handle dynamic content loading
        var delays = [100, 300, 500, 1000, 2000];
        delays.forEach(function(delay) {
            setTimeout(scrollToFragment, delay);
        });
    }
})();
"""


def inject_gist_preview_js(output_dir):
    """Inject gist preview JavaScript into all HTML files in the output directory."""
    output_dir = Path(output_dir)
    for html_file in output_dir.glob("*.html"):
        content = html_file.read_text(encoding="utf-8")
        # Insert the gist preview JS before the closing </body> tag
        if "</body>" in content:
            content = content.replace("</body>", f"<script>{GIST_PREVIEW_JS}</script>\n</body>")
            html_file.write_text(content, encoding="utf-8")


def create_gist(output_dir, public=False):
    """Create a GitHub gist from the HTML files in output_dir.

    Returns the gist ID on success, or raises click.ClickException on failure.
    """
    output_dir = Path(output_dir)
    html_files = list(output_dir.glob("*.html"))
    if not html_files:
        raise click.ClickException("No HTML files found to upload to gist.")

    # Build the gh gist create command
    # gh gist create file1 file2 ... --public/--private
    cmd = ["gh", "gist", "create"]
    cmd.extend(str(f) for f in sorted(html_files))
    if public:
        cmd.append("--public")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )
        # Output is the gist URL, e.g., https://gist.github.com/username/GIST_ID
        gist_url = result.stdout.strip()
        # Extract gist ID from URL
        gist_id = gist_url.rstrip("/").split("/")[-1]
        return gist_id, gist_url
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() if e.stderr else str(e)
        raise click.ClickException(f"Failed to create gist: {error_msg}")
    except FileNotFoundError:
        raise click.ClickException("gh CLI not found. Install it from https://cli.github.com/ and run 'gh auth login'.")


def is_url(path: str) -> bool:
    """Check if a path is a URL (starts with http:// or https://)."""
    return isinstance(path, str) and (path.startswith("http://") or path.startswith("https://"))


def fetch_url_to_tempfile(url: str) -> Path:
    """Fetch a URL and save to a temporary file.

    Returns the Path to the temporary file.
    Raises click.ClickException on network errors.
    """
    try:
        response = httpx.get(url, timeout=60.0, follow_redirects=True)
        response.raise_for_status()
    except httpx.RequestError as e:
        raise click.ClickException(f"Failed to fetch URL: {e}")
    except httpx.HTTPStatusError as e:
        raise click.ClickException(f"Failed to fetch URL: {e.response.status_code} {e.response.reason_phrase}")

    url_path = url.split("?")[0]
    if url_path.endswith(".jsonl"):
        suffix = ".jsonl"
    elif url_path.endswith(".json"):
        suffix = ".json"
    else:
        suffix = ".jsonl"

    url_name = Path(url_path).stem or "session"

    temp_dir = Path(tempfile.gettempdir())
    temp_file = temp_dir / f"claude-url-{url_name}{suffix}"
    temp_file.write_text(response.text, encoding="utf-8")
    return temp_file


def generate_pagination_html(current_page, total_pages):
    return _macros.pagination(current_page, total_pages)


def generate_index_pagination_html(total_pages):
    """Generate pagination for index page where Index is current (first page)."""
    return _macros.index_pagination(total_pages)


def _tool_display_name_from_source_format(source_format: str) -> str:
    if source_format == "codex_rollout":
        return "Codex"
    return "Claude Code"


def generate_html(
    json_path,
    output_dir,
    github_repo=None,
    *,
    session_label=None,
    tool_display_name=None,
    output_mode: str = "merge",
    prune_pages: bool = False,
    project_root: Path | None = None,
):
    output_dir = Path(output_dir)
    prepare_output_dir(output_dir=output_dir, mode=output_mode, project_root=project_root)

    # Load session file (supports both JSON and JSONL)
    data = parse_session_file(json_path)

    loglines = data.get("loglines", [])
    source_format = data.get("source_format", "")

    if tool_display_name is None:
        tool_display_name = _tool_display_name_from_source_format(source_format)

    transcript_title = f"{tool_display_name} transcript"

    # Auto-detect GitHub repo if not provided
    if github_repo is None:
        github_repo = _detect_github_repo_from_meta(data.get("meta")) or detect_github_repo(loglines)
        if github_repo:
            _LOGGER.info("Auto-detected GitHub repo: %s", github_repo)
        else:
            _LOGGER.warning("Could not auto-detect GitHub repo. Commit links will be disabled.")

    # Set module-level variable for render functions
    global _github_repo
    _github_repo = github_repo

    conversations = []
    current_conv = None
    for entry in loglines:
        log_type = entry.get("type")
        timestamp = entry.get("timestamp", "")
        is_compact_summary = entry.get("isCompactSummary", False)
        message_data = entry.get("message", {})
        if not message_data:
            continue
        # Convert message dict to JSON string for compatibility with existing render functions
        message_json = json.dumps(message_data)
        is_user_prompt = False
        user_text = None
        if log_type == "user":
            content = message_data.get("content", "")
            text = extract_text_from_content(content)
            if text:
                is_user_prompt = True
                user_text = text
        if is_user_prompt:
            if current_conv:
                conversations.append(current_conv)
            current_conv = {
                "user_text": user_text,
                "timestamp": timestamp,
                "messages": [(log_type, message_json, timestamp)],
                "is_continuation": bool(is_compact_summary),
            }
        elif current_conv:
            current_conv["messages"].append((log_type, message_json, timestamp))
    if current_conv:
        conversations.append(current_conv)

    total_convs = len(conversations)
    total_pages = (total_convs + PROMPTS_PER_PAGE - 1) // PROMPTS_PER_PAGE
    search_index = _build_search_index(conversations=conversations, total_pages=total_pages)

    for page_num in range(1, total_pages + 1):
        start_idx = (page_num - 1) * PROMPTS_PER_PAGE
        end_idx = min(start_idx + PROMPTS_PER_PAGE, total_convs)
        page_convs = conversations[start_idx:end_idx]
        messages_html = []
        for conv in page_convs:
            is_first = True
            for log_type, message_json, timestamp in conv["messages"]:
                msg_html = render_message(log_type, message_json, timestamp)
                if msg_html:
                    # Wrap continuation summaries in collapsed details
                    if is_first and conv.get("is_continuation"):
                        msg_html = f'<details class="continuation"><summary>Session continuation summary</summary>{msg_html}</details>'
                    messages_html.append(msg_html)
                is_first = False
        pagination_html = generate_pagination_html(page_num, total_pages)
        page_template = get_template("page.html")
        page_content = page_template.render(
            css=CSS,
            js=JS,
            transcript_title=transcript_title,
            session_label=session_label,
            tool_display_name=tool_display_name,
            page_num=page_num,
            total_pages=total_pages,
            pagination_html=pagination_html,
            messages_html="".join(messages_html),
        )
        (output_dir / f"page-{page_num:03d}.html").write_text(page_content, encoding="utf-8")
        _LOGGER.info("Generated page-%03d.html", page_num)

    # Calculate overall stats and collect all commits for timeline
    total_tool_counts = {}
    total_messages = 0
    all_commits = []  # (timestamp, hash, message, page_num, conv_index)
    for i, conv in enumerate(conversations):
        total_messages += len(conv["messages"])
        stats = analyze_conversation(conv["messages"])
        for tool, count in stats["tool_counts"].items():
            total_tool_counts[tool] = total_tool_counts.get(tool, 0) + count
        page_num = (i // PROMPTS_PER_PAGE) + 1
        for commit_hash, commit_msg, commit_ts in stats["commits"]:
            all_commits.append((commit_ts, commit_hash, commit_msg, page_num, i))
    total_tool_calls = sum(total_tool_counts.values())
    total_commits = len(all_commits)

    # Build timeline items: prompts and commits merged by timestamp
    timeline_items = []

    # Add prompts
    prompt_num = 0
    for i, conv in enumerate(conversations):
        if conv.get("is_continuation"):
            continue
        if conv["user_text"].startswith("Stop hook feedback:"):
            continue
        prompt_num += 1
        page_num = (i // PROMPTS_PER_PAGE) + 1
        msg_id = make_msg_id(conv["timestamp"])
        link = f"page-{page_num:03d}.html#{msg_id}"
        rendered_content = render_markdown_text(conv["user_text"])

        # Collect all messages including from subsequent continuation conversations
        # This ensures long_texts from continuations appear with the original prompt
        all_messages = list(conv["messages"])
        for j in range(i + 1, len(conversations)):
            if not conversations[j].get("is_continuation"):
                break
            all_messages.extend(conversations[j]["messages"])

        # Analyze conversation for stats (excluding commits from inline display now)
        stats = analyze_conversation(all_messages)
        tool_stats_str = format_tool_stats(stats["tool_counts"])

        long_texts_html = ""
        for lt in stats["long_texts"]:
            rendered_lt = render_markdown_text(lt)
            long_texts_html += _macros.index_long_text(rendered_lt)

        stats_html = _macros.index_stats(tool_stats_str, long_texts_html)

        item_html = _macros.index_item(prompt_num, link, conv["timestamp"], rendered_content, stats_html)
        timeline_items.append((conv["timestamp"], "prompt", item_html))

    # Add commits as separate timeline items
    for commit_ts, commit_hash, commit_msg, page_num, conv_idx in all_commits:
        item_html = _macros.index_commit(commit_hash, commit_msg, commit_ts, _github_repo)
        timeline_items.append((commit_ts, "commit", item_html))

    # Sort by timestamp
    timeline_items.sort(key=lambda x: x[0])
    index_items = [item[2] for item in timeline_items]

    index_pagination = generate_index_pagination_html(total_pages)
    index_template = get_template("index.html")
    index_content = index_template.render(
        css=CSS,
        js=JS,
        transcript_title=transcript_title,
        session_label=session_label,
        tool_display_name=tool_display_name,
        pagination_html=index_pagination,
        prompt_num=prompt_num,
        total_messages=total_messages,
        total_tool_calls=total_tool_calls,
        total_commits=total_commits,
        total_pages=total_pages,
        index_items_html="".join(index_items),
        search_index_json=_json_for_script(search_index),
    )
    index_path = output_dir / "index.html"
    index_path.write_text(index_content, encoding="utf-8")
    _LOGGER.info(
        "Generated %s (%s prompts, %s pages)",
        index_path.resolve(),
        total_convs,
        total_pages,
    )
    if prune_pages:
        prune_stale_pages(output_dir=output_dir, total_pages=total_pages)


def generate_html_from_session_data(
    session_data,
    output_dir,
    github_repo=None,
    *,
    session_label=None,
    tool_display_name=None,
):
    """Generate HTML from session data dict (instead of file path)."""
    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True, parents=True)

    loglines = session_data.get("loglines", [])
    source_format = session_data.get("source_format", "")

    if tool_display_name is None:
        tool_display_name = _tool_display_name_from_source_format(source_format)

    transcript_title = f"{tool_display_name} transcript"

    if github_repo is None:
        github_repo = _detect_github_repo_from_meta(session_data.get("meta")) or detect_github_repo(loglines)
        if github_repo:
            _LOGGER.info("Auto-detected GitHub repo: %s", github_repo)
        else:
            _LOGGER.warning("Could not auto-detect GitHub repo. Commit links will be disabled.")

    global _github_repo
    _github_repo = github_repo

    conversations = []
    current_conv = None
    for entry in loglines:
        log_type = entry.get("type")
        timestamp = entry.get("timestamp", "")
        is_compact_summary = entry.get("isCompactSummary", False)
        message_data = entry.get("message", {})
        if not message_data:
            continue
        message_json = json.dumps(message_data)
        is_user_prompt = False
        user_text = None
        if log_type == "user":
            content = message_data.get("content", "")
            text = extract_text_from_content(content)
            if text:
                is_user_prompt = True
                user_text = text
        if is_user_prompt:
            if current_conv:
                conversations.append(current_conv)
            current_conv = {
                "user_text": user_text,
                "timestamp": timestamp,
                "messages": [(log_type, message_json, timestamp)],
                "is_continuation": bool(is_compact_summary),
            }
        elif current_conv:
            current_conv["messages"].append((log_type, message_json, timestamp))
    if current_conv:
        conversations.append(current_conv)

    total_convs = len(conversations)
    total_pages = (total_convs + PROMPTS_PER_PAGE - 1) // PROMPTS_PER_PAGE
    search_index = _build_search_index(conversations=conversations, total_pages=total_pages)

    for page_num in range(1, total_pages + 1):
        start_idx = (page_num - 1) * PROMPTS_PER_PAGE
        end_idx = min(start_idx + PROMPTS_PER_PAGE, total_convs)
        page_convs = conversations[start_idx:end_idx]
        messages_html = []
        for conv in page_convs:
            is_first = True
            for log_type, message_json, timestamp in conv["messages"]:
                msg_html = render_message(log_type, message_json, timestamp)
                if msg_html:
                    if is_first and conv.get("is_continuation"):
                        msg_html = (
                            '<details class="continuation"><summary>Session continuation summary</summary>'
                            f"{msg_html}</details>"
                        )
                    messages_html.append(msg_html)
                is_first = False
        pagination_html = generate_pagination_html(page_num, total_pages)
        page_template = get_template("page.html")
        page_content = page_template.render(
            css=CSS,
            js=JS,
            transcript_title=transcript_title,
            session_label=session_label,
            tool_display_name=tool_display_name,
            page_num=page_num,
            total_pages=total_pages,
            pagination_html=pagination_html,
            messages_html="".join(messages_html),
        )
        (output_dir / f"page-{page_num:03d}.html").write_text(page_content, encoding="utf-8")
        _LOGGER.info("Generated page-%03d.html", page_num)

    total_tool_counts = {}
    total_messages = 0
    all_commits = []
    for i, conv in enumerate(conversations):
        total_messages += len(conv["messages"])
        stats = analyze_conversation(conv["messages"])
        for tool, count in stats["tool_counts"].items():
            total_tool_counts[tool] = total_tool_counts.get(tool, 0) + count
        page_num = (i // PROMPTS_PER_PAGE) + 1
        for commit_hash, commit_msg, commit_ts in stats["commits"]:
            all_commits.append((commit_ts, commit_hash, commit_msg, page_num, i))
    total_tool_calls = sum(total_tool_counts.values())
    total_commits = len(all_commits)

    timeline_items = []
    prompt_num = 0
    for i, conv in enumerate(conversations):
        if conv.get("is_continuation"):
            continue
        if conv["user_text"].startswith("Stop hook feedback:"):
            continue
        prompt_num += 1
        page_num = (i // PROMPTS_PER_PAGE) + 1
        msg_id = make_msg_id(conv["timestamp"])
        link = f"page-{page_num:03d}.html#{msg_id}"
        rendered_content = render_markdown_text(conv["user_text"])

        all_messages = list(conv["messages"])
        for j in range(i + 1, len(conversations)):
            if not conversations[j].get("is_continuation"):
                break
            all_messages.extend(conversations[j]["messages"])

        stats = analyze_conversation(all_messages)
        tool_stats_str = format_tool_stats(stats["tool_counts"])

        long_texts_html = ""
        for lt in stats["long_texts"]:
            rendered_lt = render_markdown_text(lt)
            long_texts_html += _macros.index_long_text(rendered_lt)

        stats_html = _macros.index_stats(tool_stats_str, long_texts_html)
        item_html = _macros.index_item(prompt_num, link, conv["timestamp"], rendered_content, stats_html)
        timeline_items.append((conv["timestamp"], "prompt", item_html))

    for commit_ts, commit_hash, commit_msg, page_num, conv_idx in all_commits:
        item_html = _macros.index_commit(commit_hash, commit_msg, commit_ts, _github_repo)
        timeline_items.append((commit_ts, "commit", item_html))

    timeline_items.sort(key=lambda x: x[0])
    index_items = [item[2] for item in timeline_items]

    index_pagination = generate_index_pagination_html(total_pages)
    index_template = get_template("index.html")
    index_content = index_template.render(
        css=CSS,
        js=JS,
        transcript_title=transcript_title,
        session_label=session_label,
        tool_display_name=tool_display_name,
        pagination_html=index_pagination,
        prompt_num=prompt_num,
        total_messages=total_messages,
        total_tool_calls=total_tool_calls,
        total_commits=total_commits,
        total_pages=total_pages,
        index_items_html="".join(index_items),
        search_index_json=_json_for_script(search_index),
    )
    index_path = output_dir / "index.html"
    index_path.write_text(index_content, encoding="utf-8")
    _LOGGER.info(
        "Generated %s (%s prompts, %s pages)",
        index_path.resolve(),
        total_convs,
        total_pages,
    )
