"""Convert Codex CLI and Claude Code session logs to HTML transcripts."""

import json
import html
import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import click
from click_default_group import DefaultGroup
import httpx
from jinja2 import Environment, PackageLoader
import markdown
import questionary

# Set up Jinja2 environment
_jinja_env = Environment(
    loader=PackageLoader("ai_code_sessions", "templates"),
    autoescape=True,
)

# Load macros template and expose macros
_macros_template = _jinja_env.get_template("macros.html")
_macros = _macros_template.module


def get_template(name):
    """Get a Jinja2 template by name."""
    return _jinja_env.get_template(name)


# Regex to match git commit output: [branch hash] message
COMMIT_PATTERN = re.compile(r"\[[\w\-/]+ ([a-f0-9]{7,})\] (.+?)(?:\n|$)")

# Regex to detect GitHub repo from git push output (e.g., github.com/owner/repo/pull/new/branch)
GITHUB_REPO_PATTERN = re.compile(
    r"github\.com/([a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+)/pull/new/"
)

PROMPTS_PER_PAGE = 5
LONG_TEXT_THRESHOLD = (
    300  # Characters - text blocks longer than this are shown in index
)


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
                    if (
                        obj.get("type") == "user"
                        and not obj.get("isMeta")
                        and obj.get("message", {}).get("content")
                    ):
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
    result.sort(
        key=lambda p: p["sessions"][0]["mtime"] if p["sessions"] else 0, reverse=True
    )

    return result


def generate_batch_html(
    source_folder, output_dir, include_agents=False, progress_callback=None
):
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
                generate_html(session["path"], session_dir)
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
                progress_callback(
                    project["name"], session_name, processed_count, total_session_count
                )

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
                is_error = _infer_is_error_from_exec_output(output)
                block = {"type": "tool_result", "content": output, "is_error": is_error}
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
    tmp = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".schema.json", delete=False
    )
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
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return digest[:16]


def _append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False))
        f.write("\n")


def _load_existing_run_ids(entries_path: Path) -> set[str]:
    run_ids: set[str] = set()
    if not entries_path.exists():
        return run_ids
    try:
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
        email = (
            subprocess.check_output(
                ["git", "config", "--get", "user.email"],
                cwd=str(project_root),
                stderr=subprocess.DEVNULL,
                text=True,
            )
            .strip()
        )
        if email:
            return email
    except Exception:
        pass

    try:
        name = (
            subprocess.check_output(
                ["git", "config", "--get", "user.name"],
                cwd=str(project_root),
                stderr=subprocess.DEVNULL,
                text=True,
            )
            .strip()
        )
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

    pending_tool_call = None

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

                if _tool_name_is_apply_patch(tool_name):
                    patch_text = _extract_patch_text(tool_input)
                    if patch_text:
                        file_ops = _parse_apply_patch_file_ops(patch_text)
                        touched_created |= set(file_ops["created"])
                        touched_modified |= set(file_ops["modified"])
                        touched_deleted |= set(file_ops["deleted"])
                        touched_moved.extend(file_ops["moved"])
                        call["patch_snippet"] = _truncate_text(patch_text, 12000)
                        patch_files: set[str] = set(file_ops["created"]) | set(file_ops["modified"]) | set(
                            file_ops["deleted"]
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
                pending_tool_call = call
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
                if pending_tool_call is not None and pending_tool_call.get("cmd"):
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

                if pending_tool_call is not None and pending_tool_call.get("result") is None:
                    pending_tool_call["result"] = result_obj
                    if pending_tool_call.get("is_test") and pending_tool_call.get("cmd"):
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
                                "cmd": pending_tool_call["cmd"],
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


def _run_codex_changelog_evaluator(*, prompt: str, schema_path: Path, cd: Path | None = None, model: str | None = None) -> dict:
    codex_bin = shutil.which("codex")
    if not codex_bin:
        raise click.ClickException("codex CLI not found on PATH (required for changelog generation)")

    out_file = tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=".codex-last-message.json", delete=False
    )
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
        or "rate_limit" in lower
        or "rate limit" in lower
        or "too many requests" in lower
        or re.search(r"\\b429\\b", lower) is not None
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
        digest = _build_changelog_digest(
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

            def _run_eval(d: dict) -> dict:
                prompt = _build_codex_changelog_prompt(digest=d)
                if evaluator_value == "codex":
                    if schema_path is None:
                        raise click.ClickException("Internal error: missing Codex schema path")
                    return _run_codex_changelog_evaluator(
                        prompt=prompt,
                        schema_path=schema_path,
                        cd=project_root,
                        model=evaluator_model,
                    )
                return _run_claude_changelog_evaluator(
                    prompt=prompt,
                    json_schema=_CHANGELOG_CODEX_OUTPUT_SCHEMA,
                    cd=project_root,
                    model=evaluator_model,
                    max_thinking_tokens=claude_max_thinking_tokens,
                )

            try:
                evaluator_out = _run_eval(digest)
            except Exception as e:
                # Some sessions are too large to fit in a single evaluator prompt.
                # Retry once with a budgeted digest before recording a failure.
                if _looks_like_context_window_error(str(e)):
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

        index_html_path = session_dir_abs / "index.html"
        if not index_html_path.exists():
            trace_path = session_dir_abs / "trace.html"
            if trace_path.exists():
                index_html_path = trace_path

        entry = {
            "schema_version": CHANGELOG_ENTRY_SCHEMA_VERSION,
            "run_id": run_id,
            "created_at": _now_iso8601(),
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
            "touched_files": digest.get("delta", {}).get("touched_files", {"created": [], "modified": [], "deleted": [], "moved": []}),
            "tests": digest.get("delta", {}).get("tests", []),
            "commits": digest.get("delta", {}).get("commits", []),
            "notes": notes.strip() if isinstance(notes, str) and notes.strip() else None,
        }

        _append_jsonl(entries_path, entry)
        return True, run_id, "appended"
    except Exception as e:
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
            error=str(e),
            source_jsonl=source_jsonl,
            source_match_json=source_match_json,
        )
        if halt_on_429 and _looks_like_usage_limit_error(str(e)):
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

    if not start or not end:
        return None
    start_dt = _parse_iso8601(start)
    end_dt = _parse_iso8601(end)
    if start_dt is None or end_dt is None:
        return None

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
    first = _peek_first_jsonl_object(filepath)
    last = _read_last_jsonl_object(filepath)
    if not isinstance(first, dict):
        return None, None, None, None

    start_dt = _parse_iso8601(first.get("timestamp", ""))
    end_dt = _parse_iso8601(last.get("timestamp", "")) if isinstance(last, dict) else None
    cwd = first.get("cwd")
    session_id = first.get("sessionId")
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
    base = Path.home() / ".codex" / "sessions"
    if not base.exists():
        raise click.ClickException(f"Codex sessions directory not found: {base}")

    window_start = start_dt - timedelta(minutes=15)
    window_end = end_dt + timedelta(minutes=15)

    # Restrict search to a few day-folders around the session, based on local time.
    candidate_dirs = set()
    for dt in (start_dt, end_dt):
        local = dt.astimezone()
        for offset in (-1, 0, 1):
            d = (local.date() + timedelta(days=offset))
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


def render_markdown_text(text):
    if not text:
        return ""
    return markdown.markdown(text, extensions=["fenced_code", "tables"])


def is_json_like(text):
    if not text or not isinstance(text, str):
        return False
    text = text.strip()
    return (text.startswith("{") and text.endswith("}")) or (
        text.startswith("[") and text.endswith("]")
    )


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
                    parts.append(
                        _macros.commit_card(commit_hash, commit_msg, _github_repo)
                    )
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
    return all(
        isinstance(block, dict) and block.get("type") == "tool_result"
        for block in content
    )


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
            content = content.replace(
                "</body>", f"<script>{GIST_PREVIEW_JS}</script>\n</body>"
            )
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
        raise click.ClickException(
            "gh CLI not found. Install it from https://cli.github.com/ and run 'gh auth login'."
        )


def generate_pagination_html(current_page, total_pages):
    return _macros.pagination(current_page, total_pages)


def generate_index_pagination_html(total_pages):
    """Generate pagination for index page where Index is current (first page)."""
    return _macros.index_pagination(total_pages)


def _tool_display_name_from_source_format(source_format: str) -> str:
    if source_format == "codex_rollout":
        return "Codex"
    return "Claude Code"


def generate_html(json_path, output_dir, github_repo=None, *, session_label=None, tool_display_name=None):
    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True)

    # Load session file (supports both JSON and JSONL)
    data = parse_session_file(json_path)

    loglines = data.get("loglines", [])
    source_format = data.get("source_format", "")

    if tool_display_name is None:
        tool_display_name = _tool_display_name_from_source_format(source_format)

    transcript_title = f"{tool_display_name} transcript"

    # Auto-detect GitHub repo if not provided
    if github_repo is None:
        github_repo = detect_github_repo(loglines)
        if github_repo:
            print(f"Auto-detected GitHub repo: {github_repo}")
        else:
            print(
                "Warning: Could not auto-detect GitHub repo. Commit links will be disabled."
            )

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
        (output_dir / f"page-{page_num:03d}.html").write_text(
            page_content, encoding="utf-8"
        )
        print(f"Generated page-{page_num:03d}.html")

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

        item_html = _macros.index_item(
            prompt_num, link, conv["timestamp"], rendered_content, stats_html
        )
        timeline_items.append((conv["timestamp"], "prompt", item_html))

    # Add commits as separate timeline items
    for commit_ts, commit_hash, commit_msg, page_num, conv_idx in all_commits:
        item_html = _macros.index_commit(
            commit_hash, commit_msg, commit_ts, _github_repo
        )
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
    )
    index_path = output_dir / "index.html"
    index_path.write_text(index_content, encoding="utf-8")
    print(
        f"Generated {index_path.resolve()} ({total_convs} prompts, {total_pages} pages)"
    )


@click.group(cls=DefaultGroup, default="local", default_if_no_args=True)
@click.version_option(None, "-v", "--version", package_name="ai-code-sessions")
def cli():
    """Convert Codex and Claude Code session logs to mobile-friendly HTML pages."""
    pass


@cli.command("setup")
@click.option(
    "--project-root",
    help="Target git repo root to write per-repo config (defaults to git toplevel of CWD).",
)
@click.option(
    "--global/--no-global",
    "write_global",
    default=True,
    help="Write a global config file for this user.",
)
@click.option(
    "--repo/--no-repo",
    "write_repo",
    default=True,
    help="Write a per-repo config file inside the target project root.",
)
@click.option("--force", is_flag=True, help="Overwrite existing config files.")
def setup_cmd(project_root, write_global, write_repo, force):
    """Interactive setup wizard (writes config files and optional .gitignore entries)."""
    root = (
        Path(project_root).resolve()
        if project_root
        else (_git_toplevel(Path.cwd()) or Path.cwd().resolve())
    )

    global_path = _global_config_path()
    repo_path = _repo_config_path(root)

    existing_cfg = _load_config(project_root=root)
    default_actor = (
        _config_get(existing_cfg, "changelog.actor")
        or os.environ.get("CTX_ACTOR")
        or _detect_actor(project_root=root)
    )
    default_tz = (
        _config_get(existing_cfg, "ctx.tz")
        or os.environ.get("CTX_TZ")
        or "America/Los_Angeles"
    )
    default_changelog_enabled = bool(_config_get(existing_cfg, "changelog.enabled", False))
    default_evaluator = (
        _config_get(existing_cfg, "changelog.evaluator")
        or "codex"
    )
    default_model = _config_get(existing_cfg, "changelog.model") or ""
    default_claude_tokens = _config_get(existing_cfg, "changelog.claude_thinking_tokens") or 8192

    if write_repo and root.exists():
        click.echo(f"Repo config:   {repo_path}")
    if write_global:
        click.echo(f"Global config: {global_path}")

    actor = questionary.text("Changelog actor (e.g. GitHub username):", default=str(default_actor)).ask()
    if actor is None:
        raise click.ClickException("Setup aborted.")

    tz = questionary.text("Time zone for session folder names (IANA TZ):", default=str(default_tz)).ask()
    if tz is None:
        raise click.ClickException("Setup aborted.")

    changelog_enabled = questionary.confirm(
        "Enable changelog generation by default?",
        default=default_changelog_enabled,
    ).ask()
    if changelog_enabled is None:
        raise click.ClickException("Setup aborted.")

    evaluator = default_evaluator
    model = str(default_model or "").strip()
    claude_tokens = None
    if changelog_enabled:
        evaluator = questionary.select(
            "Changelog evaluator:",
            choices=["codex", "claude"],
            default=str(default_evaluator),
        ).ask()
        if evaluator is None:
            raise click.ClickException("Setup aborted.")
        evaluator = str(evaluator).strip().lower()

        model = questionary.text(
            "Default model override (blank for tool default):",
            default=str(model),
        ).ask()
        if model is None:
            raise click.ClickException("Setup aborted.")
        model = str(model).strip()

        if evaluator == "claude":
            raw = questionary.text(
                "Claude max thinking tokens (blank for default 8192):",
                default=str(default_claude_tokens or 8192),
            ).ask()
            if raw is None:
                raise click.ClickException("Setup aborted.")
            raw = str(raw).strip()
            if raw:
                try:
                    claude_tokens = int(raw)
                except ValueError:
                    raise click.ClickException("Claude thinking tokens must be an integer")
                if claude_tokens <= 0:
                    raise click.ClickException("Claude thinking tokens must be positive")

    commit_changelog = questionary.confirm(
        "Do you want .changelog entries to be committable in this repo?",
        default=False,
    ).ask()
    if commit_changelog is None:
        raise click.ClickException("Setup aborted.")

    cfg_out: dict = {
        "ctx": {"tz": tz},
        "changelog": {"enabled": bool(changelog_enabled), "actor": actor},
    }
    if changelog_enabled:
        cfg_out["changelog"]["evaluator"] = evaluator
        if model:
            cfg_out["changelog"]["model"] = model
        if claude_tokens:
            cfg_out["changelog"]["claude_thinking_tokens"] = claude_tokens

    toml_text = _render_config_toml(cfg_out)
    if not toml_text.strip():
        raise click.ClickException("Refusing to write empty config.")

    if write_global:
        if global_path.exists() and not force:
            overwrite = questionary.confirm(
                f"Global config already exists at {global_path}. Overwrite?",
                default=False,
            ).ask()
            if overwrite is None or overwrite is False:
                click.echo("Skipped global config.")
            else:
                global_path.parent.mkdir(parents=True, exist_ok=True)
                global_path.write_text(toml_text, encoding="utf-8")
                click.echo("Wrote global config.")
        else:
            global_path.parent.mkdir(parents=True, exist_ok=True)
            global_path.write_text(toml_text, encoding="utf-8")
            click.echo("Wrote global config.")

    if write_repo:
        if repo_path.exists() and not force:
            overwrite = questionary.confirm(
                f"Repo config already exists at {repo_path}. Overwrite?",
                default=False,
            ).ask()
            if overwrite is None or overwrite is False:
                click.echo("Skipped repo config.")
            else:
                repo_path.write_text(toml_text, encoding="utf-8")
                click.echo("Wrote repo config.")
        else:
            repo_path.write_text(toml_text, encoding="utf-8")
            click.echo("Wrote repo config.")

    if not commit_changelog:
        _ensure_gitignore_ignores(root, ".changelog/")
        click.echo("Updated .gitignore to ignore .changelog/")
    else:
        click.echo("Note: ensure your repo does not ignore .changelog/ if you want to commit entries.")


@cli.command("local")
@click.option(
    "-o",
    "--output",
    type=click.Path(),
    help="Output directory. If not specified, writes to temp dir and opens in browser.",
)
@click.option(
    "-a",
    "--output-auto",
    is_flag=True,
    help="Auto-name output subdirectory based on session filename (uses -o as parent, or current dir).",
)
@click.option(
    "--repo",
    help="GitHub repo (owner/name) for commit links. Auto-detected from git push output if not specified.",
)
@click.option(
    "--gist",
    is_flag=True,
    help="Upload to GitHub Gist and output a gisthost.github.io URL.",
)
@click.option(
    "--json",
    "include_json",
    is_flag=True,
    help="Include the original JSONL session file in the output directory.",
)
@click.option(
    "--open",
    "open_browser",
    is_flag=True,
    help="Open the generated index.html in your default browser (default if no -o specified).",
)
@click.option(
    "--limit",
    default=10,
    help="Maximum number of sessions to show (default: 10)",
)
def local_cmd(output, output_auto, repo, gist, include_json, open_browser, limit):
    """Select and convert a local Claude Code session to HTML."""
    projects_folder = Path.home() / ".claude" / "projects"

    if not projects_folder.exists():
        click.echo(f"Projects folder not found: {projects_folder}")
        click.echo("No local Claude Code sessions available.")
        return

    click.echo("Loading local sessions...")
    results = find_local_sessions(projects_folder, limit=limit)

    if not results:
        click.echo("No local sessions found.")
        return

    # Build choices for questionary
    choices = []
    for filepath, summary in results:
        stat = filepath.stat()
        mod_time = datetime.fromtimestamp(stat.st_mtime)
        size_kb = stat.st_size / 1024
        date_str = mod_time.strftime("%Y-%m-%d %H:%M")
        # Truncate summary if too long
        if len(summary) > 50:
            summary = summary[:47] + "..."
        display = f"{date_str}  {size_kb:5.0f} KB  {summary}"
        choices.append(questionary.Choice(title=display, value=filepath))

    selected = questionary.select(
        "Select a session to convert:",
        choices=choices,
    ).ask()

    if selected is None:
        click.echo("No session selected.")
        return

    session_file = selected

    # Determine output directory and whether to open browser
    # If no -o specified, use temp dir and open browser by default
    auto_open = output is None and not gist and not output_auto
    if output_auto:
        # Use -o as parent dir (or current dir), with auto-named subdirectory
        parent_dir = Path(output) if output else Path(".")
        output = parent_dir / session_file.stem
    elif output is None:
        output = Path(tempfile.gettempdir()) / f"claude-session-{session_file.stem}"

    output = Path(output)
    generate_html(session_file, output, github_repo=repo)

    # Show output directory
    click.echo(f"Output: {output.resolve()}")

    # Copy JSONL file to output directory if requested
    if include_json:
        output.mkdir(exist_ok=True)
        json_dest = output / session_file.name
        shutil.copy(session_file, json_dest)
        json_size_kb = json_dest.stat().st_size / 1024
        click.echo(f"JSONL: {json_dest} ({json_size_kb:.1f} KB)")

    if gist:
        # Inject gist preview JS and create gist
        inject_gist_preview_js(output)
        click.echo("Creating GitHub gist...")
        gist_id, gist_url = create_gist(output)
        preview_url = f"https://gisthost.github.io/?{gist_id}/index.html"
        click.echo(f"Gist: {gist_url}")
        click.echo(f"Preview: {preview_url}")

    if open_browser or auto_open:
        index_url = (output / "index.html").resolve().as_uri()
        webbrowser.open(index_url)


def is_url(path):
    """Check if a path is a URL (starts with http:// or https://)."""
    return path.startswith("http://") or path.startswith("https://")


def fetch_url_to_tempfile(url):
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
        raise click.ClickException(
            f"Failed to fetch URL: {e.response.status_code} {e.response.reason_phrase}"
        )

    # Determine file extension from URL
    url_path = url.split("?")[0]  # Remove query params
    if url_path.endswith(".jsonl"):
        suffix = ".jsonl"
    elif url_path.endswith(".json"):
        suffix = ".json"
    else:
        suffix = ".jsonl"  # Default to JSONL

    # Extract a name from the URL for the temp file
    url_name = Path(url_path).stem or "session"

    temp_dir = Path(tempfile.gettempdir())
    temp_file = temp_dir / f"claude-url-{url_name}{suffix}"
    temp_file.write_text(response.text, encoding="utf-8")
    return temp_file


@cli.command("json")
@click.argument("json_file", type=click.Path())
@click.option(
    "-o",
    "--output",
    type=click.Path(),
    help="Output directory. If not specified, writes to temp dir and opens in browser.",
)
@click.option(
    "-a",
    "--output-auto",
    is_flag=True,
    help="Auto-name output subdirectory based on filename (uses -o as parent, or current dir).",
)
@click.option(
    "--repo",
    help="GitHub repo (owner/name) for commit links. Auto-detected from git push output if not specified.",
)
@click.option(
    "--gist",
    is_flag=True,
    help="Upload to GitHub Gist and output a gisthost.github.io URL.",
)
@click.option(
    "--json",
    "include_json",
    is_flag=True,
    help="Include the original JSON session file in the output directory.",
)
@click.option(
    "--open",
    "open_browser",
    is_flag=True,
    help="Open the generated index.html in your default browser (default if no -o specified).",
)
@click.option("--label", help="Optional human-friendly label to display in the transcript header.")
def json_cmd(json_file, output, output_auto, repo, gist, include_json, open_browser, label):
    """Convert a session JSON/JSONL file (Codex or Claude) or URL to HTML."""
    # Handle URL input
    if is_url(json_file):
        click.echo(f"Fetching {json_file}...")
        temp_file = fetch_url_to_tempfile(json_file)
        json_file_path = temp_file
        # Use URL path for naming
        url_name = Path(json_file.split("?")[0]).stem or "session"
    else:
        # Validate that local file exists
        json_file_path = Path(json_file)
        if not json_file_path.exists():
            raise click.ClickException(f"File not found: {json_file}")
        url_name = None

    # Determine output directory and whether to open browser
    # If no -o specified, use temp dir and open browser by default
    auto_open = output is None and not gist and not output_auto
    if output_auto:
        # Use -o as parent dir (or current dir), with auto-named subdirectory
        parent_dir = Path(output) if output else Path(".")
        output = parent_dir / (url_name or json_file_path.stem)
    elif output is None:
        output = (
            Path(tempfile.gettempdir())
            / f"ai-session-{url_name or json_file_path.stem}"
        )

    output = Path(output)
    generate_html(json_file_path, output, github_repo=repo, session_label=label)

    # Show output directory
    click.echo(f"Output: {output.resolve()}")

    # Copy JSON file to output directory if requested
    if include_json:
        output.mkdir(exist_ok=True)
        json_dest = output / json_file_path.name
        shutil.copy(json_file_path, json_dest)
        json_size_kb = json_dest.stat().st_size / 1024
        click.echo(f"JSON: {json_dest} ({json_size_kb:.1f} KB)")

    if gist:
        # Inject gist preview JS and create gist
        inject_gist_preview_js(output)
        click.echo("Creating GitHub gist...")
        gist_id, gist_url = create_gist(output)
        preview_url = f"https://gisthost.github.io/?{gist_id}/index.html"
        click.echo(f"Gist: {gist_url}")
        click.echo(f"Preview: {preview_url}")

    if open_browser or auto_open:
        index_url = (output / "index.html").resolve().as_uri()
        webbrowser.open(index_url)


@cli.command("find-source")
@click.option(
    "--tool",
    required=True,
    type=click.Choice(["codex", "claude"], case_sensitive=False),
    help="Which CLI session source to search for.",
)
@click.option("--cwd", required=True, help="Working directory used to start the CLI session.")
@click.option("--project-root", required=True, help="Git project root for the session (used for Claude lookup).")
@click.option("--start", required=True, help="Session start timestamp (ISO 8601).")
@click.option("--end", required=True, help="Session end timestamp (ISO 8601).")
@click.option(
    "--debug-json",
    type=click.Path(),
    help="Optional path to write debug candidate data as JSON.",
)
def find_source_cmd(tool, cwd, project_root, start, end, debug_json):
    """Find the native session log file that best matches the given time window."""
    result = find_best_source_file(
        tool=tool,
        cwd=cwd,
        project_root=project_root,
        start=start,
        end=end,
    )
    if debug_json:
        Path(debug_json).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    click.echo(result["best"]["path"])


@cli.command("export-latest")
@click.option(
    "--tool",
    required=True,
    type=click.Choice(["codex", "claude"], case_sensitive=False),
    help="Which CLI session source to export.",
)
@click.option("--cwd", required=True, help="Working directory used to start the CLI session.")
@click.option("--project-root", required=True, help="Git project root for the session (used for Claude lookup).")
@click.option("--start", required=True, help="Session start timestamp (ISO 8601).")
@click.option("--end", required=True, help="Session end timestamp (ISO 8601).")
@click.option("-o", "--output", required=True, type=click.Path(), help="Output directory for HTML transcript.")
@click.option("--label", help="Optional human-friendly label to display in the transcript header.")
@click.option(
    "--repo",
    help="GitHub repo (owner/name) for commit links. Auto-detected from git push output if not specified.",
)
@click.option(
    "--json",
    "include_json",
    is_flag=True,
    help="Copy the original JSON/JSONL session file into the output directory.",
)
@click.option(
    "--open",
    "open_browser",
    is_flag=True,
    help="Open the generated index.html in your default browser.",
)
@click.option(
    "--changelog/--no-changelog",
    default=_env_truthy("AI_CODE_SESSIONS_CHANGELOG") or _env_truthy("CTX_CHANGELOG"),
    help="Append a .changelog/<actor>/entries.jsonl entry for this run (best-effort).",
)
@click.option(
    "--changelog-evaluator",
    type=click.Choice(["codex", "claude"], case_sensitive=False),
    default=None,
    show_default="codex",
    help="Changelog evaluator to use (defaults to env CTX_CHANGELOG_EVALUATOR / AI_CODE_SESSIONS_CHANGELOG_EVALUATOR).",
)
@click.option("--changelog-actor", help="Override actor recorded in the changelog entry.")
@click.option(
    "--changelog-model",
    help="Override model for changelog evaluation (defaults to env CTX_CHANGELOG_MODEL / AI_CODE_SESSIONS_CHANGELOG_MODEL).",
)
def export_latest_cmd(
    tool,
    cwd,
    project_root,
    start,
    end,
    output,
    label,
    repo,
    include_json,
    open_browser,
    changelog,
    changelog_evaluator,
    changelog_actor,
    changelog_model,
):
    """Export the session that ran in the given time window to HTML."""
    output_dir = Path(output)
    output_dir.mkdir(exist_ok=True, parents=True)
    project_root_path = Path(project_root).resolve()
    cfg = _load_config(project_root=project_root_path)

    click_ctx = click.get_current_context(silent=True)
    if click_ctx and click_ctx.get_parameter_source("changelog") == click.core.ParameterSource.DEFAULT:
        env_present = (
            os.environ.get("AI_CODE_SESSIONS_CHANGELOG") is not None
            or os.environ.get("CTX_CHANGELOG") is not None
        )
        if not env_present:
            cfg_enabled = _config_get(cfg, "changelog.enabled")
            if isinstance(cfg_enabled, bool):
                changelog = cfg_enabled

    match = find_best_source_file(
        tool=tool,
        cwd=cwd,
        project_root=project_root,
        start=start,
        end=end,
    )
    source_path = Path(match["best"]["path"])

    # Write matching debug info into the output directory for traceability.
    source_match_path = output_dir / "source_match.json"
    source_match_path.write_text(
        json.dumps(match, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    generate_html(source_path, output_dir, github_repo=repo, session_label=label)

    json_dest = None
    if include_json:
        json_dest = output_dir / source_path.name
        shutil.copy(source_path, json_dest)
        click.echo(f"JSON: {json_dest}")

    click.echo(f"Output: {output_dir.resolve()}")

    export_runs_path = output_dir / "export_runs.jsonl"
    previous_run = _read_last_jsonl_object(export_runs_path)
    previous_run_id = (
        previous_run.get("changelog_run_id")
        if isinstance(previous_run, dict) and isinstance(previous_run.get("changelog_run_id"), str)
        else None
    )

    changelog_run_id = None
    changelog_appended = None
    changelog_evaluator_used = None
    changelog_model_used = None
    changelog_claude_thinking_tokens_used = None
    if changelog:
        source_jsonl_for_digest = json_dest or source_path
        cfg_actor = _config_get(cfg, "changelog.actor")
        cfg_actor_value = cfg_actor.strip() if isinstance(cfg_actor, str) and cfg_actor.strip() else None
        actor_value = changelog_actor or cfg_actor_value or _detect_actor(project_root=project_root_path)
        actor_slug = _slugify_actor(actor_value)
        entries_rel = f".changelog/{actor_slug}/entries.jsonl"
        failures_rel = f".changelog/{actor_slug}/failures.jsonl"
        try:
            env_evaluator = (_env_first("CTX_CHANGELOG_EVALUATOR", "AI_CODE_SESSIONS_CHANGELOG_EVALUATOR") or "").strip()
            cfg_evaluator = _config_get(cfg, "changelog.evaluator")
            cfg_evaluator_value = cfg_evaluator.strip() if isinstance(cfg_evaluator, str) and cfg_evaluator.strip() else ""
            evaluator_value = (
                (changelog_evaluator or "").strip()
                or env_evaluator
                or cfg_evaluator_value
                or "codex"
            ).lower()
            env_model = (_env_first("CTX_CHANGELOG_MODEL", "AI_CODE_SESSIONS_CHANGELOG_MODEL") or "").strip()
            cfg_model = _config_get(cfg, "changelog.model")
            cfg_model_value = cfg_model.strip() if isinstance(cfg_model, str) and cfg_model.strip() else ""
            model_value = (changelog_model or "").strip() or env_model or cfg_model_value or None
            claude_tokens = None
            if evaluator_value == "claude":
                raw_tokens = _env_first(
                    "CTX_CHANGELOG_CLAUDE_THINKING_TOKENS",
                    "AI_CODE_SESSIONS_CHANGELOG_CLAUDE_THINKING_TOKENS",
                )
                if not raw_tokens:
                    cfg_tokens = _config_get(cfg, "changelog.claude_thinking_tokens")
                    if isinstance(cfg_tokens, int):
                        raw_tokens = str(cfg_tokens)
                if raw_tokens:
                    try:
                        claude_tokens = int(raw_tokens)
                    except ValueError:
                        raise click.ClickException(
                            "CTX_CHANGELOG_CLAUDE_THINKING_TOKENS must be an integer (or unset)"
                        )
                    if claude_tokens <= 0:
                        raise click.ClickException(
                            "CTX_CHANGELOG_CLAUDE_THINKING_TOKENS must be a positive integer"
                        )

            changelog_evaluator_used = evaluator_value
            changelog_model_used = model_value
            changelog_claude_thinking_tokens_used = claude_tokens

            changelog_appended, changelog_run_id, changelog_status = _generate_and_append_changelog_entry(
                tool=(tool or "unknown").lower(),
                label=label,
                cwd=cwd,
                project_root=project_root_path,
                session_dir=output_dir,
                start=start,
                end=end,
                source_jsonl=Path(source_jsonl_for_digest).resolve(),
                source_match_json=source_match_path.resolve(),
                prior_prompts=3,
                actor=actor_value,
                evaluator=evaluator_value,
                evaluator_model=model_value,
                claude_max_thinking_tokens=claude_tokens,
                continuation_of_run_id=previous_run_id,
            )
            if changelog_appended and changelog_status == "appended":
                click.echo(f"Changelog: appended ({entries_rel}, run_id={changelog_run_id})")
            else:
                click.echo(f"Changelog: not updated (run_id={changelog_run_id}; see {failures_rel})")
        except Exception as e:
            click.echo(f"Changelog: FAILED ({e})")

    # Always record the export run metadata for later backfills/debugging.
    _append_jsonl(
        export_runs_path,
        {
            "schema_version": 1,
            "created_at": _now_iso8601(),
            "tool": (tool or "unknown").lower(),
            "label": label,
            "start": start,
            "end": end,
            "cwd": cwd,
            "project_root": str(project_root_path),
            "output_dir": str(output_dir.resolve()),
            "source_path": str(source_path.resolve()),
            "copied_jsonl": str(json_dest.resolve()) if json_dest else None,
            "source_match_json": str(source_match_path.resolve()),
            "changelog_enabled": bool(changelog),
            "changelog_run_id": changelog_run_id,
            "changelog_appended": changelog_appended,
            "changelog_evaluator": changelog_evaluator_used,
            "changelog_model": changelog_model_used,
            "changelog_claude_thinking_tokens": changelog_claude_thinking_tokens_used,
        },
    )

    if open_browser:
        index_url = (output_dir / "index.html").resolve().as_uri()
        webbrowser.open(index_url)


def _sanitize_ctx_label(label: str) -> str:
    value = (label or "").strip()
    if not value:
        return ""
    value = value.replace(" ", "_")
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
    value = re.sub(r"_+", "_", value)
    value = value.strip("_")
    return value


def _ctx_stamp(tz_name: str) -> str:
    tz = ZoneInfo(tz_name)
    return datetime.now(tz).strftime("%Y-%m-%d-%H%M")


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


def _session_dir_matches_label(session_dir: Path, san_label: str) -> bool:
    if not san_label:
        return False
    base = session_dir.name
    if base.endswith(f"_{san_label}"):
        return True
    return bool(re.search(rf"_{re.escape(san_label)}_\\d+$", base))


def _find_resume_session_dir(
    base_dir: Path,
    san_label: str,
    session_id: str | None,
) -> Path | None:
    if not base_dir.exists():
        return None

    all_dirs = [p for p in base_dir.iterdir() if p.is_dir()]
    if not all_dirs:
        return None

    if san_label:
        candidates = [d for d in all_dirs if _session_dir_matches_label(d, san_label)]
        if candidates:
            candidates_sorted = sorted(candidates, key=lambda p: p.name)
            if session_id:
                for d in candidates_sorted[-25:]:
                    sid = _session_dir_session_id(d)
                    if sid and sid == session_id:
                        return d
            return candidates_sorted[-1]

    if session_id:
        recent = sorted(all_dirs, key=lambda p: p.name)[-50:]
        for d in recent:
            sid = _session_dir_session_id(d)
            if sid and sid == session_id:
                return d

    return None


def _is_resume_run(tool: str, args: list[str]) -> tuple[bool, str | None]:
    tool = (tool or "").lower()
    if not args:
        return False, None

    if tool == "codex":
        if args[0] != "resume":
            return False, None
        resume_id = None
        if len(args) > 1 and not str(args[1]).startswith("-"):
            resume_id = str(args[1])
        return True, resume_id

    if tool == "claude":
        if "--fork-session" in args:
            return False, None
        if "--continue" in args or "-c" in args:
            return True, None
        for i, a in enumerate(args):
            if a in ("--resume", "-r", "--session-id"):
                resume_id = None
                if i + 1 < len(args) and not str(args[i + 1]).startswith("-"):
                    resume_id = str(args[i + 1])
                return True, resume_id
        return False, None

    return False, None


@cli.command(
    "ctx",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("label", required=False)
@click.option(
    "--tool",
    type=click.Choice(["codex", "claude"], case_sensitive=False),
    help="Which CLI to run under the wrapper.",
)
@click.option("--codex", "tool_codex", is_flag=True, help="Shortcut for --tool codex.")
@click.option("--claude", "tool_claude", is_flag=True, help="Shortcut for --tool claude.")
@click.option(
    "--tz",
    default=lambda: os.environ.get("CTX_TZ") or "America/Los_Angeles",
    show_default="America/Los_Angeles (or env CTX_TZ)",
    help="Time zone used for naming the session output directory.",
)
@click.option("--repo", help="GitHub repo (owner/name) for commit links (optional).")
@click.option("--open", "open_browser", is_flag=True, help="Open index.html after export.")
@click.option(
    "--changelog/--no-changelog",
    default=_env_truthy("AI_CODE_SESSIONS_CHANGELOG") or _env_truthy("CTX_CHANGELOG"),
    help="Append a .changelog/<actor>/entries.jsonl entry after export (best-effort).",
)
@click.option(
    "--changelog-evaluator",
    type=click.Choice(["codex", "claude"], case_sensitive=False),
    default=None,
    show_default="codex",
    help="Changelog evaluator to use (defaults to env CTX_CHANGELOG_EVALUATOR / AI_CODE_SESSIONS_CHANGELOG_EVALUATOR).",
)
@click.option("--changelog-actor", help="Override actor recorded in the changelog entry.")
@click.option(
    "--changelog-model",
    help="Override model for changelog evaluation (defaults to env CTX_CHANGELOG_MODEL / AI_CODE_SESSIONS_CHANGELOG_MODEL).",
)
@click.pass_context
def ctx_cmd(
    ctx: click.Context,
    label: str | None,
    tool: str | None,
    tool_codex: bool,
    tool_claude: bool,
    tz: str,
    repo: str | None,
    open_browser: bool,
    changelog: bool,
    changelog_evaluator: str | None,
    changelog_actor: str | None,
    changelog_model: str | None,
):
    """Run Codex or Claude, then export the matching session transcript on exit."""
    if tool_codex:
        tool = "codex"
    if tool_claude:
        tool = "claude"
    tool = (tool or "").strip().lower() or None
    if tool not in ("codex", "claude"):
        raise click.ClickException("Missing or invalid --tool (use --codex or --claude)")

    project_root = _git_toplevel(Path.cwd()) or Path.cwd().resolve()
    cfg = _load_config(project_root=project_root)

    if ctx.get_parameter_source("tz") == click.core.ParameterSource.DEFAULT and os.environ.get("CTX_TZ") is None:
        cfg_tz = _config_get(cfg, "ctx.tz")
        if isinstance(cfg_tz, str) and cfg_tz.strip():
            tz = cfg_tz.strip()

    if ctx.get_parameter_source("changelog") == click.core.ParameterSource.DEFAULT:
        env_present = (
            os.environ.get("AI_CODE_SESSIONS_CHANGELOG") is not None
            or os.environ.get("CTX_CHANGELOG") is not None
        )
        if not env_present:
            cfg_enabled = _config_get(cfg, "changelog.enabled")
            if isinstance(cfg_enabled, bool):
                changelog = cfg_enabled

    if tool == "codex":
        base_dir = project_root / ".codex" / "sessions"
        cfg_cmd = _config_get(cfg, "ctx.codex_cmd")
        cfg_cmd_value = cfg_cmd.strip() if isinstance(cfg_cmd, str) and cfg_cmd.strip() else None
        tool_cmd = os.environ.get("CTX_CODEX_CMD") or cfg_cmd_value or "codex"
    else:
        base_dir = project_root / ".claude" / "sessions"
        cfg_cmd = _config_get(cfg, "ctx.claude_cmd")
        cfg_cmd_value = cfg_cmd.strip() if isinstance(cfg_cmd, str) and cfg_cmd.strip() else None
        tool_cmd = os.environ.get("CTX_CLAUDE_CMD") or cfg_cmd_value or "claude"
    base_dir.mkdir(parents=True, exist_ok=True)

    label_value = (label or "").strip()
    san_label = _sanitize_ctx_label(label_value)

    extra_args = [str(a) for a in (ctx.args or [])]
    is_resume, resume_session_id = _is_resume_run(tool, extra_args)

    session_path = None
    if is_resume:
        session_path = _find_resume_session_dir(base_dir, san_label, resume_session_id)

    if session_path is None:
        try:
            stamp = _ctx_stamp(tz)
        except Exception as e:
            raise click.ClickException(f"Invalid --tz {tz!r}: {e}")

        if san_label:
            session_path = base_dir / f"{stamp}_{san_label}"
        else:
            session_path = base_dir / stamp

        base_path = session_path
        i = 0
        while session_path.exists():
            i += 1
            session_path = Path(f"{base_path}_{i}")
        session_path.mkdir(parents=True, exist_ok=True)

    cwd_value = str(Path.cwd().resolve())
    start_ts = datetime.now(timezone.utc).isoformat()

    cmd = [tool_cmd, *extra_args]
    try:
        completed = subprocess.run(cmd)
        rc = int(completed.returncode)
    except FileNotFoundError:
        raise click.ClickException(
            f"Command not found: {tool_cmd!r} (set CTX_CODEX_CMD/CTX_CLAUDE_CMD to override)"
        )
    except KeyboardInterrupt:
        rc = 130
    except Exception as e:
        raise click.ClickException(f"Failed to run {tool_cmd!r}: {e}")

    end_ts = datetime.now(timezone.utc).isoformat()

    try:
        ctx.invoke(
            export_latest_cmd,
            tool=tool,
            cwd=cwd_value,
            project_root=str(project_root),
            start=start_ts,
            end=end_ts,
            output=str(session_path),
            label=label_value or None,
            repo=repo,
            include_json=True,
            open_browser=open_browser,
            changelog=changelog,
            changelog_evaluator=changelog_evaluator,
            changelog_actor=changelog_actor,
            changelog_model=changelog_model,
        )
    except Exception as e:
        click.echo(
            f"ctx: warning: transcript export failed ({e}); output dir: {session_path}",
            err=True,
        )

    raise SystemExit(rc)


@cli.group("changelog")
def changelog_cli():
    """Generate and manage per-repo changelog entries."""
    pass


@changelog_cli.command("backfill")
@click.option(
    "--project-root",
    help="Target git repo root that contains .codex/.claude session outputs (defaults to git toplevel of CWD).",
)
@click.option(
    "--sessions-dir",
    multiple=True,
    help="One or more session parent dirs to scan (defaults to <project-root>/.codex/sessions and <project-root>/.claude/sessions).",
)
@click.option("--actor", help="Override actor recorded in each changelog entry.")
@click.option(
    "--evaluator",
    type=click.Choice(["codex", "claude"], case_sensitive=False),
    default="codex",
    show_default=True,
    help="Which CLI to use for changelog evaluation.",
)
@click.option("--model", help="Override model for the selected evaluator.")
@click.option("--dry-run", is_flag=True, help="Print what would be done without writing entries.")
@click.option("--limit", type=int, help="Maximum number of runs to process.")
def changelog_backfill_cmd(project_root, sessions_dir, actor, evaluator, model, dry_run, limit):
    """Backfill .changelog entries from existing ctx session output directories."""
    root = Path(project_root).resolve() if project_root else (_git_toplevel(Path.cwd()) or Path.cwd().resolve())

    claude_tokens = None
    raw_tokens = _env_first(
        "CTX_CHANGELOG_CLAUDE_THINKING_TOKENS",
        "AI_CODE_SESSIONS_CHANGELOG_CLAUDE_THINKING_TOKENS",
    )
    if raw_tokens:
        try:
            claude_tokens = int(raw_tokens)
        except ValueError:
            raise click.ClickException("CTX_CHANGELOG_CLAUDE_THINKING_TOKENS must be an integer (or unset)")
        if claude_tokens <= 0:
            raise click.ClickException("CTX_CHANGELOG_CLAUDE_THINKING_TOKENS must be a positive integer")

    halted = False
    if sessions_dir:
        bases = [Path(p).expanduser() for p in sessions_dir]
        bases = [b if b.is_absolute() else (root / b) for b in bases]
    else:
        bases = [root / ".codex" / "sessions", root / ".claude" / "sessions"]

    processed = 0
    for base in bases:
        if limit is not None and processed >= limit:
            break
        if not base.exists():
            continue

        tool_guess = "unknown"
        if base.parent.name == ".codex":
            tool_guess = "codex"
        elif base.parent.name == ".claude":
            tool_guess = "claude"

        session_dirs = [p for p in base.iterdir() if p.is_dir()]
        session_dirs.sort(key=lambda p: p.name)

        for session_dir in session_dirs:
            if limit is not None and processed >= limit:
                break

            export_runs_path = session_dir / "export_runs.jsonl"
            source_match_path = session_dir / "source_match.json"
            legacy_meta = _legacy_ctx_metadata(session_dir)

            runs = []
            if export_runs_path.exists():
                runs = _read_jsonl_objects(export_runs_path)
            else:
                synthetic = {}
                if source_match_path.exists():
                    try:
                        synthetic_match = json.loads(source_match_path.read_text(encoding="utf-8"))
                        best = synthetic_match.get("best") if isinstance(synthetic_match, dict) else {}
                        if isinstance(best, dict):
                            synthetic["start"] = best.get("start")
                            synthetic["end"] = best.get("end")
                            synthetic["tool"] = tool_guess
                    except Exception:
                        pass

                # Legacy PTY sessions: infer timestamps and matching hints.
                if legacy_meta:
                    synthetic.setdefault("start", legacy_meta.get("start"))
                    synthetic.setdefault("end", legacy_meta.get("end"))
                    synthetic.setdefault("tool", legacy_meta.get("tool") or tool_guess)
                    synthetic.setdefault("label", legacy_meta.get("label"))
                    synthetic.setdefault("cwd", legacy_meta.get("cwd"))
                    synthetic.setdefault("project_root", legacy_meta.get("project_root"))
                    synthetic.setdefault("codex_resume_id", legacy_meta.get("codex_resume_id"))
                runs = [synthetic]

            prev_run_id = None
            label_guess = _derive_label_from_session_dir(session_dir)

            for run in runs:
                if limit is not None and processed >= limit:
                    break
                start = run.get("start") if isinstance(run, dict) else None
                end = run.get("end") if isinstance(run, dict) else None
                tool = (run.get("tool") if isinstance(run, dict) else None) or tool_guess
                label = (run.get("label") if isinstance(run, dict) else None) or label_guess
                run_cwd = run.get("cwd") if isinstance(run, dict) else None
                codex_resume_id = run.get("codex_resume_id") if isinstance(run, dict) else None

                copied_jsonl = (
                    Path(run.get("copied_jsonl")).expanduser()
                    if isinstance(run, dict) and run.get("copied_jsonl")
                    else None
                )
                if copied_jsonl and not copied_jsonl.is_absolute():
                    copied_jsonl = (root / copied_jsonl).resolve()
                if copied_jsonl is None or not copied_jsonl.exists():
                    # Prefer the copied file that matches source_match.json, if present.
                    if source_match_path.exists():
                        try:
                            match_obj = json.loads(source_match_path.read_text(encoding="utf-8"))
                            best = match_obj.get("best") if isinstance(match_obj, dict) else None
                            best_path = best.get("path") if isinstance(best, dict) else None
                            if isinstance(best_path, str) and best_path:
                                candidate = session_dir / Path(best_path).name
                                if candidate.exists():
                                    copied_jsonl = candidate
                        except Exception:
                            pass
                if copied_jsonl is None or not copied_jsonl.exists():
                    copied_jsonl = _choose_copied_jsonl_for_session_dir(session_dir)

                if copied_jsonl is None or not copied_jsonl.exists():
                    copied_jsonl = _maybe_copy_native_jsonl_into_legacy_session_dir(
                        tool=(tool or "unknown").lower(),
                        session_dir=session_dir,
                        start=start,
                        end=end,
                        cwd=run_cwd or (legacy_meta.get("cwd") if legacy_meta else None),
                        codex_resume_id=codex_resume_id or (legacy_meta.get("codex_resume_id") if legacy_meta else None),
                    )

                if (not start or not end) and copied_jsonl and copied_jsonl.exists():
                    # Last-resort: infer run bounds from copied JSONL boundaries.
                    if tool == "codex":
                        sdt, edt, _, _ = _codex_rollout_session_times(copied_jsonl)
                    else:
                        sdt, edt, _, _ = _claude_session_times(copied_jsonl)
                    if sdt and edt:
                        start = start or sdt.isoformat()
                        end = end or edt.isoformat()

                if not start or not end or copied_jsonl is None or not copied_jsonl.exists():
                    click.echo(f"Backfill: skipping {session_dir} (missing timestamps or JSONL)")
                    continue

                if dry_run:
                    click.echo(
                        f"Backfill: would process {tool} {session_dir.name} "
                        f"({start} → {end}) using {copied_jsonl.name}"
                    )
                    processed += 1
                    continue

                appended, run_id, status = _generate_and_append_changelog_entry(
                    tool=(tool or "unknown").lower(),
                    label=label,
                    cwd=str(root),
                    project_root=root,
                    session_dir=session_dir,
                    start=start,
                    end=end,
                    source_jsonl=copied_jsonl.resolve(),
                    source_match_json=source_match_path.resolve(),
                    prior_prompts=3,
                    actor=actor,
                    evaluator=evaluator,
                    evaluator_model=model,
                    claude_max_thinking_tokens=claude_tokens,
                    continuation_of_run_id=prev_run_id,
                    halt_on_429=True,
                )
                prev_run_id = run_id
                processed += 1
                if status == "rate_limited":
                    click.echo(f"Backfill: halted (usage limit reached) run_id={run_id} ({session_dir.name})")
                    halted = True
                    break
                elif status == "exists":
                    click.echo(f"Backfill: skipped (already exists) run_id={run_id} ({session_dir.name})")
                elif status == "failed":
                    click.echo(f"Backfill: failed run_id={run_id} ({session_dir.name})")
                else:
                    click.echo(f"Backfill: appended run_id={run_id} ({session_dir.name})")

            if halted:
                break

        if halted:
            break

    if halted:
        click.echo(f"Backfill halted: processed {processed} run(s).")
    else:
        click.echo(f"Backfill complete: processed {processed} run(s).")


def resolve_credentials(token, org_uuid):
    """Resolve token and org_uuid from arguments or auto-detect.

    Returns (token, org_uuid) tuple.
    Raises click.ClickException if credentials cannot be resolved.
    """
    # Get token
    if token is None:
        token = get_access_token_from_keychain()
        if token is None:
            if platform.system() == "Darwin":
                raise click.ClickException(
                    "Could not retrieve access token from macOS keychain. "
                    "Make sure you are logged into Claude Code, or provide --token."
                )
            else:
                raise click.ClickException(
                    "On non-macOS platforms, you must provide --token with your access token."
                )

    # Get org UUID
    if org_uuid is None:
        org_uuid = get_org_uuid_from_config()
        if org_uuid is None:
            raise click.ClickException(
                "Could not find organization UUID in ~/.claude.json. "
                "Provide --org-uuid with your organization UUID."
            )

    return token, org_uuid


def format_session_for_display(session_data):
    """Format a session for display in the list or picker.

    Returns a formatted string.
    """
    session_id = session_data.get("id", "unknown")
    title = session_data.get("title", "Untitled")
    created_at = session_data.get("created_at", "")
    # Truncate title if too long
    if len(title) > 60:
        title = title[:57] + "..."
    return f"{session_id}  {created_at[:19] if created_at else 'N/A':19}  {title}"


def generate_html_from_session_data(session_data, output_dir, github_repo=None, *, session_label=None, tool_display_name=None):
    """Generate HTML from session data dict (instead of file path)."""
    output_dir = Path(output_dir)
    output_dir.mkdir(exist_ok=True, parents=True)

    loglines = session_data.get("loglines", [])
    source_format = session_data.get("source_format", "")

    if tool_display_name is None:
        tool_display_name = _tool_display_name_from_source_format(source_format)

    transcript_title = f"{tool_display_name} transcript"

    # Auto-detect GitHub repo if not provided
    if github_repo is None:
        github_repo = detect_github_repo(loglines)
        if github_repo:
            click.echo(f"Auto-detected GitHub repo: {github_repo}")

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
        (output_dir / f"page-{page_num:03d}.html").write_text(
            page_content, encoding="utf-8"
        )
        click.echo(f"Generated page-{page_num:03d}.html")

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

        item_html = _macros.index_item(
            prompt_num, link, conv["timestamp"], rendered_content, stats_html
        )
        timeline_items.append((conv["timestamp"], "prompt", item_html))

    # Add commits as separate timeline items
    for commit_ts, commit_hash, commit_msg, page_num, conv_idx in all_commits:
        item_html = _macros.index_commit(
            commit_hash, commit_msg, commit_ts, _github_repo
        )
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
    )
    index_path = output_dir / "index.html"
    index_path.write_text(index_content, encoding="utf-8")
    click.echo(
        f"Generated {index_path.resolve()} ({total_convs} prompts, {total_pages} pages)"
    )


@cli.command("web")
@click.argument("session_id", required=False)
@click.option(
    "-o",
    "--output",
    type=click.Path(),
    help="Output directory. If not specified, writes to temp dir and opens in browser.",
)
@click.option(
    "-a",
    "--output-auto",
    is_flag=True,
    help="Auto-name output subdirectory based on session ID (uses -o as parent, or current dir).",
)
@click.option("--token", help="API access token (auto-detected from keychain on macOS)")
@click.option(
    "--org-uuid", help="Organization UUID (auto-detected from ~/.claude.json)"
)
@click.option(
    "--repo",
    help="GitHub repo (owner/name) for commit links. Auto-detected from git push output if not specified.",
)
@click.option(
    "--gist",
    is_flag=True,
    help="Upload to GitHub Gist and output a gisthost.github.io URL.",
)
@click.option(
    "--json",
    "include_json",
    is_flag=True,
    help="Include the JSON session data in the output directory.",
)
@click.option(
    "--open",
    "open_browser",
    is_flag=True,
    help="Open the generated index.html in your default browser (default if no -o specified).",
)
def web_cmd(
    session_id,
    output,
    output_auto,
    token,
    org_uuid,
    repo,
    gist,
    include_json,
    open_browser,
):
    """Select and convert a web session from the Claude API to HTML.

    If SESSION_ID is not provided, displays an interactive picker to select a session.
    """
    try:
        token, org_uuid = resolve_credentials(token, org_uuid)
    except click.ClickException:
        raise

    # If no session ID provided, show interactive picker
    if session_id is None:
        try:
            sessions_data = fetch_sessions(token, org_uuid)
        except httpx.HTTPStatusError as e:
            raise click.ClickException(
                f"API request failed: {e.response.status_code} {e.response.text}"
            )
        except httpx.RequestError as e:
            raise click.ClickException(f"Network error: {e}")

        sessions = sessions_data.get("data", [])
        if not sessions:
            raise click.ClickException("No sessions found.")

        # Build choices for questionary
        choices = []
        for s in sessions:
            sid = s.get("id", "unknown")
            title = s.get("title", "Untitled")
            created_at = s.get("created_at", "")
            # Truncate title if too long
            if len(title) > 50:
                title = title[:47] + "..."
            display = f"{created_at[:19] if created_at else 'N/A':19}  {title}"
            choices.append(questionary.Choice(title=display, value=sid))

        selected = questionary.select(
            "Select a session to import:",
            choices=choices,
        ).ask()

        if selected is None:
            # User cancelled
            raise click.ClickException("No session selected.")

        session_id = selected

    # Fetch the session
    click.echo(f"Fetching session {session_id}...")
    try:
        session_data = fetch_session(token, org_uuid, session_id)
    except httpx.HTTPStatusError as e:
        raise click.ClickException(
            f"API request failed: {e.response.status_code} {e.response.text}"
        )
    except httpx.RequestError as e:
        raise click.ClickException(f"Network error: {e}")

    # Determine output directory and whether to open browser
    # If no -o specified, use temp dir and open browser by default
    auto_open = output is None and not gist and not output_auto
    if output_auto:
        # Use -o as parent dir (or current dir), with auto-named subdirectory
        parent_dir = Path(output) if output else Path(".")
        output = parent_dir / session_id
    elif output is None:
        output = Path(tempfile.gettempdir()) / f"claude-session-{session_id}"

    output = Path(output)
    click.echo(f"Generating HTML in {output}/...")
    generate_html_from_session_data(session_data, output, github_repo=repo)

    # Show output directory
    click.echo(f"Output: {output.resolve()}")

    # Save JSON session data if requested
    if include_json:
        output.mkdir(exist_ok=True)
        json_dest = output / f"{session_id}.json"
        with open(json_dest, "w") as f:
            json.dump(session_data, f, indent=2)
        json_size_kb = json_dest.stat().st_size / 1024
        click.echo(f"JSON: {json_dest} ({json_size_kb:.1f} KB)")

    if gist:
        # Inject gist preview JS and create gist
        inject_gist_preview_js(output)
        click.echo("Creating GitHub gist...")
        gist_id, gist_url = create_gist(output)
        preview_url = f"https://gisthost.github.io/?{gist_id}/index.html"
        click.echo(f"Gist: {gist_url}")
        click.echo(f"Preview: {preview_url}")

    if open_browser or auto_open:
        index_url = (output / "index.html").resolve().as_uri()
        webbrowser.open(index_url)


@cli.command("all")
@click.option(
    "-s",
    "--source",
    type=click.Path(exists=True),
    help="Source directory containing Claude projects (default: ~/.claude/projects).",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(),
    default="./claude-archive",
    help="Output directory for the archive (default: ./claude-archive).",
)
@click.option(
    "--include-agents",
    is_flag=True,
    help="Include agent-* session files (excluded by default).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be converted without creating files.",
)
@click.option(
    "--open",
    "open_browser",
    is_flag=True,
    help="Open the generated archive in your default browser.",
)
@click.option(
    "-q",
    "--quiet",
    is_flag=True,
    help="Suppress all output except errors.",
)
def all_cmd(source, output, include_agents, dry_run, open_browser, quiet):
    """Convert all local Claude Code sessions to a browsable HTML archive.

    Creates a directory structure with:
    - Master index listing all projects
    - Per-project pages listing sessions
    - Individual session transcripts
    """
    # Default source folder
    if source is None:
        source = Path.home() / ".claude" / "projects"
    else:
        source = Path(source)

    if not source.exists():
        raise click.ClickException(f"Source directory not found: {source}")

    output = Path(output)

    if not quiet:
        click.echo(f"Scanning {source}...")

    projects = find_all_sessions(source, include_agents=include_agents)

    if not projects:
        if not quiet:
            click.echo("No sessions found.")
        return

    # Calculate totals
    total_sessions = sum(len(p["sessions"]) for p in projects)

    if not quiet:
        click.echo(f"Found {len(projects)} projects with {total_sessions} sessions")

    if dry_run:
        # Dry-run always outputs (it's the point of dry-run), but respects --quiet
        if not quiet:
            click.echo("\nDry run - would convert:")
            for project in projects:
                click.echo(
                    f"\n  {project['name']} ({len(project['sessions'])} sessions)"
                )
                for session in project["sessions"][:3]:  # Show first 3
                    mod_time = datetime.fromtimestamp(session["mtime"])
                    click.echo(
                        f"    - {session['path'].stem} ({mod_time.strftime('%Y-%m-%d')})"
                    )
                if len(project["sessions"]) > 3:
                    click.echo(f"    ... and {len(project['sessions']) - 3} more")
        return

    if not quiet:
        click.echo(f"\nGenerating archive in {output}...")

    # Progress callback for non-quiet mode
    def on_progress(project_name, session_name, current, total):
        if not quiet and current % 10 == 0:
            click.echo(f"  Processed {current}/{total} sessions...")

    # Generate the archive using the library function
    stats = generate_batch_html(
        source,
        output,
        include_agents=include_agents,
        progress_callback=on_progress,
    )

    # Report any failures
    if stats["failed_sessions"]:
        click.echo(f"\nWarning: {len(stats['failed_sessions'])} session(s) failed:")
        for failure in stats["failed_sessions"]:
            click.echo(
                f"  {failure['project']}/{failure['session']}: {failure['error']}"
            )

    if not quiet:
        click.echo(
            f"\nGenerated archive with {stats['total_projects']} projects, "
            f"{stats['total_sessions']} sessions"
        )
        click.echo(f"Output: {output.resolve()}")

    if open_browser:
        index_url = (output / "index.html").resolve().as_uri()
        webbrowser.open(index_url)


def main():
    cli()
