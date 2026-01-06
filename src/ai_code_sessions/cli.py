import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import click
from click_default_group import DefaultGroup
import httpx
import questionary

from .core import (
    CSS,
    JS,
    _append_jsonl,
    _append_log_line,
    _backfill_log_path,
    _choose_copied_jsonl_for_session_dir,
    _claude_session_times,
    _codex_rollout_session_times,
    _collect_repo_sessions,
    _config_get,
    _derive_label_from_session_dir,
    _detect_actor,
    _ensure_gitignore_ignores,
    _env_first,
    _env_truthy,
    _format_local_dt,
    _generate_and_append_changelog_entry,
    _git_toplevel,
    _global_config_path,
    _legacy_ctx_metadata,
    _load_config,
    _maybe_copy_native_jsonl_into_legacy_session_dir,
    _now_iso8601,
    _read_jsonl_objects,
    _read_last_jsonl_object,
    _render_config_toml,
    _repo_config_path,
    _slugify_actor,
    configure_logging,
    create_gist,
    fetch_session,
    fetch_sessions,
    fetch_url_to_tempfile,
    find_all_sessions,
    find_best_source_file,
    find_local_sessions,
    format_session_for_display,
    generate_batch_html,
    generate_html,
    generate_html_from_session_data,
    get_template,
    inject_gist_preview_js,
    is_url,
    prepare_output_dir,
    resolve_config_with_provenance,
    resolve_credentials,
)


@click.group(cls=DefaultGroup, default="local", default_if_no_args=True)
@click.version_option(None, "--version", package_name="ai-code-sessions")
@click.option(
    "-v",
    "--verbose",
    count=True,
    help="Increase log verbosity (repeatable).",
)
@click.option(
    "--log-file",
    type=click.Path(),
    help="Write logs to a file (or set AI_CODE_SESSIONS_LOG_DIR).",
)
def cli(verbose, log_file):
    """Convert Codex and Claude Code session logs to mobile-friendly HTML pages."""
    log_path = Path(log_file) if log_file else None
    if log_path is None:
        log_dir = os.environ.get("AI_CODE_SESSIONS_LOG_DIR")
        if log_dir:
            log_path = Path(log_dir) / "ai-code-sessions.log"
    configure_logging(verbosity=verbose, log_file=log_path)


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
    root = Path(project_root).resolve() if project_root else (_git_toplevel(Path.cwd()) or Path.cwd().resolve())

    global_path = _global_config_path()
    repo_path = _repo_config_path(root)

    existing_cfg = _load_config(project_root=root)
    default_actor = (
        _config_get(existing_cfg, "changelog.actor") or os.environ.get("CTX_ACTOR") or _detect_actor(project_root=root)
    )
    default_tz = _config_get(existing_cfg, "ctx.tz") or os.environ.get("CTX_TZ") or "America/Los_Angeles"
    default_changelog_enabled = bool(_config_get(existing_cfg, "changelog.enabled", False))
    default_evaluator = (
        _config_get(existing_cfg, "changelog.evaluator") or os.environ.get("CTX_CHANGELOG_EVALUATOR") or "codex"
    )
    default_model = _config_get(existing_cfg, "changelog.model") or ""
    default_claude_tokens = _config_get(existing_cfg, "changelog.claude_thinking_tokens") or 8192

    actor = questionary.text("Changelog actor (e.g. GitHub username):", default=str(default_actor)).ask()
    if actor is None:
        return

    tz = questionary.text("Time zone for session folder names (IANA TZ):", default=str(default_tz)).ask()
    if tz is None:
        return

    changelog_enabled = questionary.confirm(
        "Enable changelog generation by default?",
        default=default_changelog_enabled,
    ).ask()
    if changelog_enabled is None:
        return

    evaluator = None
    model = None
    claude_tokens = None
    if changelog_enabled:
        evaluator = questionary.select(
            "Which evaluator should be used for changelog generation?",
            choices=["codex", "claude"],
            default=default_evaluator,
        ).ask()
        if evaluator is None:
            return
        evaluator = evaluator.strip().lower()
        if evaluator == "codex":
            model = questionary.text(
                "Optional model override (e.g. gpt-5.2-codex, gpt-5.1-codex-mini); leave blank for default:",
                default=default_model,
            ).ask()
            if model is None:
                return
            model = model.strip() or None
        elif evaluator == "claude":
            raw = questionary.text(
                "Optional Claude thinking token budget (default: 8192):",
                default=str(default_claude_tokens),
            ).ask()
            if raw is None:
                return
            raw = raw.strip()
            if raw:
                try:
                    claude_tokens = int(raw)
                except ValueError:
                    raise click.ClickException("Claude thinking tokens must be an integer")
                if claude_tokens <= 0:
                    raise click.ClickException("Claude thinking tokens must be a positive integer")

    commit_changelog = questionary.confirm(
        "Do you want .changelog entries to be committable in this repo?",
        default=False,
    ).ask()
    if commit_changelog is None:
        return

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

    cfg_text = _render_config_toml(cfg_out)

    if write_global:
        global_path.parent.mkdir(parents=True, exist_ok=True)
        if global_path.exists() and not force:
            overwrite = questionary.confirm(
                f"Global config already exists at {global_path}. Overwrite?",
                default=False,
            ).ask()
            if overwrite is None or not overwrite:
                write_global = False
        if write_global:
            global_path.write_text(cfg_text, encoding="utf-8")
            click.echo(f"Wrote global config: {global_path}")

    if write_repo:
        repo_path.parent.mkdir(parents=True, exist_ok=True)
        if repo_path.exists() and not force:
            overwrite = questionary.confirm(
                f"Repo config already exists at {repo_path}. Overwrite?",
                default=False,
            ).ask()
            if overwrite is None or not overwrite:
                write_repo = False
        if write_repo:
            repo_path.write_text(cfg_text, encoding="utf-8")
            click.echo(f"Wrote repo config: {repo_path}")

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
@click.option(
    "--output-mode",
    type=click.Choice(["merge", "overwrite", "clean"], case_sensitive=False),
    default="merge",
    show_default=True,
    help="How to handle existing output directories.",
)
@click.option(
    "--prune-pages/--no-prune-pages",
    default=False,
    show_default=True,
    help="Remove stale page-*.html files beyond the new page count.",
)
@click.option("--label", help="Optional human-friendly label to display in the transcript header.")
def json_cmd(
    json_file,
    output,
    output_auto,
    repo,
    gist,
    include_json,
    open_browser,
    output_mode,
    prune_pages,
    label,
):
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
        output = Path(tempfile.gettempdir()) / f"ai-session-{url_name or json_file_path.stem}"

    output = Path(output)
    generate_html(
        json_file_path,
        output,
        github_repo=repo,
        session_label=label,
        output_mode=output_mode,
        prune_pages=prune_pages,
    )

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
    "--output-mode",
    type=click.Choice(["merge", "overwrite", "clean"], case_sensitive=False),
    default="merge",
    show_default=True,
    help="How to handle existing output directories.",
)
@click.option(
    "--prune-pages/--no-prune-pages",
    default=True,
    show_default=True,
    help="Remove stale page-*.html files beyond the new page count.",
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
    output_mode,
    prune_pages,
    changelog,
    changelog_evaluator,
    changelog_actor,
    changelog_model,
):
    """Export the session that ran in the given time window to HTML."""
    output_dir = Path(output)
    project_root_path = Path(project_root).resolve()
    cfg = _load_config(project_root=project_root_path)

    click_ctx = click.get_current_context(silent=True)
    if click_ctx and click_ctx.get_parameter_source("changelog") == click.core.ParameterSource.DEFAULT:
        env_present = (
            os.environ.get("AI_CODE_SESSIONS_CHANGELOG") is not None or os.environ.get("CTX_CHANGELOG") is not None
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

    generate_html(
        source_path,
        output_dir,
        github_repo=repo,
        session_label=label,
        output_mode=output_mode,
        prune_pages=prune_pages,
        project_root=project_root_path,
    )

    # Write matching debug info into the output directory for traceability.
    source_match_path = output_dir / "source_match.json"
    source_match_path.write_text(
        json.dumps(match, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

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
            env_evaluator = (
                _env_first("CTX_CHANGELOG_EVALUATOR", "AI_CODE_SESSIONS_CHANGELOG_EVALUATOR") or ""
            ).strip()
            cfg_evaluator = _config_get(cfg, "changelog.evaluator")
            cfg_evaluator_value = (
                cfg_evaluator.strip() if isinstance(cfg_evaluator, str) and cfg_evaluator.strip() else ""
            )
            evaluator_value = (
                (changelog_evaluator or "").strip() or env_evaluator or cfg_evaluator_value or "codex"
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
                        raise click.ClickException("CTX_CHANGELOG_CLAUDE_THINKING_TOKENS must be an integer (or unset)")
                    if claude_tokens <= 0:
                        raise click.ClickException("CTX_CHANGELOG_CLAUDE_THINKING_TOKENS must be a positive integer")

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


def _resolve_ctx_tool(*, tool: str, cfg: dict, project_root: Path) -> tuple[Path, str]:
    if tool == "codex":
        base_dir = project_root / ".codex" / "sessions"
        cfg_cmd = _config_get(cfg, "ctx.codex_cmd")
        cfg_cmd_value = cfg_cmd.strip() if isinstance(cfg_cmd, str) and cfg_cmd.strip() else None
        tool_cmd = os.environ.get("CTX_CODEX_CMD") or cfg_cmd_value or "codex"
        return base_dir, tool_cmd
    base_dir = project_root / ".claude" / "sessions"
    cfg_cmd = _config_get(cfg, "ctx.claude_cmd")
    cfg_cmd_value = cfg_cmd.strip() if isinstance(cfg_cmd, str) and cfg_cmd.strip() else None
    tool_cmd = os.environ.get("CTX_CLAUDE_CMD") or cfg_cmd_value or "claude"
    return base_dir, tool_cmd


def _resolve_ctx_tz(*, cfg: dict, tz: str | None) -> str:
    if tz:
        return tz
    cfg_tz = _config_get(cfg, "ctx.tz")
    if isinstance(cfg_tz, str) and cfg_tz.strip():
        return cfg_tz.strip()
    return os.environ.get("CTX_TZ") or "America/Los_Angeles"


def _resolve_ctx_changelog(*, cfg: dict) -> bool:
    if os.environ.get("AI_CODE_SESSIONS_CHANGELOG") is not None or os.environ.get("CTX_CHANGELOG") is not None:
        return _env_truthy("AI_CODE_SESSIONS_CHANGELOG") or _env_truthy("CTX_CHANGELOG")
    cfg_enabled = _config_get(cfg, "changelog.enabled")
    if isinstance(cfg_enabled, bool):
        return cfg_enabled
    return False


def _run_ctx_session(
    *,
    ctx: click.Context,
    tool: str,
    label_value: str | None,
    tz: str,
    repo: str | None,
    open_browser: bool,
    changelog: bool,
    changelog_evaluator: str | None,
    changelog_actor: str | None,
    changelog_model: str | None,
    project_root: Path,
    base_dir: Path,
    tool_cmd: str,
    extra_args: list[str],
    session_path_override: Path | None = None,
) -> None:
    san_label = _sanitize_ctx_label(label_value or "")

    session_path: Path | None = None
    if session_path_override is not None:
        session_path = Path(session_path_override)
        if not session_path.exists():
            raise click.ClickException(f"Session directory not found: {session_path}")
    else:
        is_resume, resume_session_id = _is_resume_run(tool, extra_args)
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

    base_dir.mkdir(parents=True, exist_ok=True)
    cwd_value = str(Path.cwd().resolve())
    start_ts = datetime.now(timezone.utc).isoformat()

    cmd = [tool_cmd, *extra_args]
    try:
        completed = subprocess.run(cmd)
        rc = int(completed.returncode)
    except FileNotFoundError:
        raise click.ClickException(f"Command not found: {tool_cmd!r} (set CTX_CODEX_CMD/CTX_CLAUDE_CMD to override)")
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
        tz = _resolve_ctx_tz(cfg=cfg, tz=None)

    if ctx.get_parameter_source("changelog") == click.core.ParameterSource.DEFAULT:
        changelog = _resolve_ctx_changelog(cfg=cfg)

    base_dir, tool_cmd = _resolve_ctx_tool(tool=tool, cfg=cfg, project_root=project_root)
    label_value = (label or "").strip()
    extra_args = [str(a) for a in (ctx.args or [])]

    _run_ctx_session(
        ctx=ctx,
        tool=tool,
        label_value=label_value,
        tz=tz,
        repo=repo,
        open_browser=open_browser,
        changelog=changelog,
        changelog_evaluator=changelog_evaluator,
        changelog_actor=changelog_actor,
        changelog_model=changelog_model,
        project_root=project_root,
        base_dir=base_dir,
        tool_cmd=tool_cmd,
        extra_args=extra_args,
    )


@cli.command("resume")
@click.argument("tool", type=click.Choice(["codex", "claude"], case_sensitive=False))
@click.option("--limit", default=50, show_default=True, help="Maximum sessions to show.")
@click.option("--latest", is_flag=True, help="Resume the newest session without prompting.")
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
def resume_cmd(
    ctx: click.Context,
    tool: str,
    limit: int,
    latest: bool,
    repo: str | None,
    open_browser: bool,
    changelog: bool,
    changelog_evaluator: str | None,
    changelog_actor: str | None,
    changelog_model: str | None,
):
    """Pick a previous session to resume with a friendly picker."""
    tool = (tool or "").strip().lower()
    project_root = _git_toplevel(Path.cwd()) or Path.cwd().resolve()
    cfg = _load_config(project_root=project_root)
    tz = _resolve_ctx_tz(cfg=cfg, tz=None)
    if ctx.get_parameter_source("changelog") == click.core.ParameterSource.DEFAULT:
        changelog = _resolve_ctx_changelog(cfg=cfg)
    base_dir, tool_cmd = _resolve_ctx_tool(tool=tool, cfg=cfg, project_root=project_root)

    sessions = _collect_repo_sessions(base_dir=base_dir, tool=tool, limit=limit, tz_name=tz)
    if not sessions:
        click.echo(f"No {tool} sessions found under {base_dir}")
        return

    if latest:
        selected = next((s for s in sessions if s.get("resume_id")), None)
        if selected is None:
            click.echo("No resumable sessions found.")
            return
    else:
        choices = []
        header = "DATE (TZ)          DUR    PG  PR  MSG  LABEL"
        choices.append(questionary.Choice(title=header, value=None, disabled=" "))
        for session in sessions:
            label = session.get("label") or session["session_dir"].name
            display_label = label
            if len(display_label) > 60:
                display_label = display_label[:57] + "..."
            display_ts = _format_local_dt(session.get("start_dt"), tz)
            duration = session.get("duration") or "--"
            pages = session.get("pages") or 0
            prompts = session.get("prompts")
            messages = session.get("messages")
            prompts_str = f"{prompts:>3}pr" if isinstance(prompts, int) else " --pr"
            messages_str = f"{messages:>4}msg" if isinstance(messages, int) else " ---msg"
            resume_id = session.get("resume_id")
            title = f"{display_ts:19}  {duration:>6}  {pages:>3}p  {prompts_str}  {messages_str}  {display_label}"
            if resume_id:
                choices.append(questionary.Choice(title=title, value=session))
            else:
                choices.append(questionary.Choice(title=title, value=None, disabled="missing resume id"))

        selected = questionary.autocomplete(
            f"Select a {tool} session to resume:",
            choices=choices,
            match_middle=True,
        ).ask()

    if selected is None:
        click.echo("No session selected.")
        return

    resume_id = selected.get("resume_id")
    if not resume_id:
        click.echo("Selected session is missing a resume id.")
        return

    if tool == "codex":
        extra_args = ["resume", resume_id]
    else:
        extra_args = ["--resume", resume_id]

    label_value = selected.get("label") or _derive_label_from_session_dir(selected["session_dir"])
    _run_ctx_session(
        ctx=ctx,
        tool=tool,
        label_value=label_value,
        tz=tz,
        repo=repo,
        open_browser=open_browser,
        changelog=changelog,
        changelog_evaluator=changelog_evaluator,
        changelog_actor=changelog_actor,
        changelog_model=changelog_model,
        project_root=project_root,
        base_dir=base_dir,
        tool_cmd=tool_cmd,
        extra_args=extra_args,
        session_path_override=selected["session_dir"],
    )


cli.add_command(resume_cmd, "ctx-resume")


@cli.group("config")
def config_cli():
    """Inspect resolved configuration."""
    pass


@config_cli.command("show")
@click.option(
    "--project-root",
    help="Target git repo root to resolve per-repo config (defaults to git toplevel of CWD).",
)
@click.option("--json", "as_json", is_flag=True, help="Output resolved config as JSON.")
def config_show_cmd(project_root, as_json):
    """Show resolved configuration values and their provenance."""
    root = Path(project_root).resolve() if project_root else (_git_toplevel(Path.cwd()) or Path.cwd().resolve())
    resolved, provenance = resolve_config_with_provenance(project_root=root)
    if as_json:
        click.echo(
            json.dumps(
                {"resolved": resolved, "provenance": provenance},
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    click.echo(f"Config (project_root={root}):")
    for key in sorted(provenance.keys()):
        value = _config_get(resolved, key)
        click.echo(f"  {key} = {value!r}  [{provenance[key]}]")


@cli.command("archive")
@click.option(
    "--project-root",
    help="Target git repo root to scan for .codex/.claude sessions (defaults to git toplevel of CWD).",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(),
    help="Output directory for the archive (default: <project_root>/.ais-archive).",
)
@click.option("--open", "open_browser", is_flag=True, help="Open the archive in your default browser.")
def archive_cmd(project_root, output, open_browser):
    """Generate a repo-level archive for .codex/.claude sessions."""
    root = Path(project_root).resolve() if project_root else (_git_toplevel(Path.cwd()) or Path.cwd().resolve())
    cfg = _load_config(project_root=root)
    tz = _resolve_ctx_tz(cfg=cfg, tz=None)
    output_dir = Path(output) if output else (root / ".ais-archive")
    prepare_output_dir(output_dir=output_dir, mode="merge", project_root=root)

    sessions_data: list[dict] = []
    for tool in ("codex", "claude"):
        base_dir = root / f".{tool}" / "sessions"
        for session in _collect_repo_sessions(base_dir=base_dir, tool=tool, limit=None, tz_name=tz):
            session_dir = session["session_dir"]
            index_path = session_dir / "index.html"
            if not index_path.exists():
                continue
            rel_link = os.path.relpath(index_path, output_dir)
            sort_dt = session.get("start_dt")
            sessions_data.append(
                {
                    "label": session.get("label") or session_dir.name,
                    "tool": tool,
                    "date": _format_local_dt(session.get("start_dt"), tz),
                    "duration": session.get("duration") or "--",
                    "pages": session.get("pages") or 0,
                    "link": rel_link,
                    "sort_dt": sort_dt,
                }
            )

    sessions_data.sort(
        key=lambda s: s.get("sort_dt") or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    for session in sessions_data:
        session.pop("sort_dt", None)

    template = get_template("repo_archive.html")
    html_content = template.render(
        transcript_title="Session Archive",
        session_label=None,
        sessions=sessions_data,
        session_count=len(sessions_data),
        css=CSS,
        js=JS,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    index_path = output_dir / "index.html"
    index_path.write_text(html_content, encoding="utf-8")
    click.echo(f"Archive: {index_path.resolve()}")

    if open_browser:
        webbrowser.open(index_path.resolve().as_uri())


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
@click.option(
    "--max-concurrency",
    type=int,
    help="Maximum number of concurrent evaluator runs (Claude evaluator only). Defaults to 5 for --evaluator claude.",
)
@click.option("--limit", type=int, help="Maximum number of runs to process.")
def changelog_backfill_cmd(project_root, sessions_dir, actor, evaluator, model, dry_run, max_concurrency, limit):
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

    evaluator_value = (evaluator or "codex").strip().lower()
    if evaluator_value not in ("codex", "claude"):
        raise click.ClickException(f"Unknown evaluator: {evaluator}")

    backfill_log = _backfill_log_path(project_root=root, actor=actor, evaluator=evaluator_value)
    if backfill_log is not None and not dry_run:
        click.echo(f"Backfill log: {backfill_log}", err=True)

    max_concurrency_value = max_concurrency
    if max_concurrency_value is None:
        max_concurrency_value = 5 if evaluator_value == "claude" else 1
    if max_concurrency_value <= 0:
        raise click.ClickException("--max-concurrency must be a positive integer")
    if evaluator_value != "claude" and max_concurrency_value != 1:
        raise click.ClickException("--max-concurrency is only supported with --evaluator claude")
    if limit is not None and max_concurrency_value > 1:
        raise click.ClickException("--limit is only supported with --max-concurrency 1")

    halted = False
    if sessions_dir:
        bases = [Path(p).expanduser() for p in sessions_dir]
        bases = [b if b.is_absolute() else (root / b) for b in bases]
    else:
        bases = [root / ".codex" / "sessions", root / ".claude" / "sessions"]

    _append_log_line(
        backfill_log if not dry_run else None,
        f"backfill_start project_root={root} evaluator={evaluator_value} model={model or ''} "
        f"actor={actor or ''} sessions_dir={[str(b) for b in bases]} max_concurrency={max_concurrency_value}",
    )

    if evaluator_value == "claude" and max_concurrency_value > 1 and not dry_run:
        stop_event = threading.Event()

        progress_enabled = os.environ.get("CTX_CHANGELOG_PROGRESS") != "0"
        progress_tty = sys.stderr.isatty()
        progress_lock = threading.Lock()
        completed_sessions = 0
        processed_runs = 0
        progress_started = time.monotonic()
        progress_stop = threading.Event()
        last_progress_len = 0

        def _progress_clear() -> None:
            nonlocal last_progress_len
            if not progress_enabled or not progress_tty:
                return
            sys.stderr.write("\r" + " " * last_progress_len + "\r")
            sys.stderr.flush()
            last_progress_len = 0

        def _progress_worker(total: int) -> None:
            nonlocal last_progress_len
            while not progress_stop.is_set():
                if stop_event.wait(timeout=0.5):
                    break
                if not progress_enabled or not progress_tty:
                    continue
                with progress_lock:
                    done = completed_sessions
                    runs = processed_runs
                    elapsed = max(time.monotonic() - progress_started, 0.1)
                per_min = runs / (elapsed / 60)
                msg = f"Evaluating candidate directories ({done}/{total}) - {per_min:.1f} runs/min"
                sys.stderr.write("\r" + msg)
                sys.stderr.flush()
                last_progress_len = len(msg)

        progress_thread: threading.Thread | None = None
        progress_stop.clear()
        progress_started = time.monotonic()
        progress_thread = threading.Thread(target=_progress_worker, args=(len(bases),), daemon=True)
        progress_thread.start()

    def _progress_cleanup() -> None:
        if evaluator_value == "claude" and max_concurrency_value > 1 and not dry_run:
            progress_stop.set()
            if progress_thread is not None:
                progress_thread.join(timeout=2)
            stop_event.set()

    def _progress_clear_line() -> None:
        if evaluator_value == "claude" and max_concurrency_value > 1 and not dry_run:
            sys.stderr.write("\r" + " " * 80 + "\r")
            sys.stderr.flush()

    if evaluator_value == "claude" and max_concurrency_value > 1 and not dry_run:
        _progress_clear()

    def _maybe_write_progress() -> None:
        nonlocal last_progress_len
        if evaluator_value != "claude" or max_concurrency_value <= 1 or dry_run:
            return
        if not progress_enabled or not progress_tty:
            return
        with progress_lock:
            done = completed_sessions
            runs = processed_runs
            elapsed = max(time.monotonic() - progress_started, 0.1)
        per_min = runs / (elapsed / 60)
        msg = f"Evaluating candidate directories ({done}/{len(bases)}) - {per_min:.1f} runs/min"
        sys.stderr.write("\r" + msg)
        sys.stderr.flush()
        last_progress_len = len(msg)

    def _print_errors(lines: list[str]) -> None:
        _progress_clear_line()
        for line in lines:
            click.echo(line)
        _maybe_write_progress()

    def _run_one_session(tool_guess: str, session_dir: Path):
        nonlocal processed_runs
        output_lines = []
        halted_local = False
        processed_local = 0

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
            if limit is not None and processed_runs >= limit:
                halted_local = True
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
                output_lines.append(f"Backfill: skipping {session_dir} (missing timestamps or JSONL)")
                continue

            if dry_run:
                output_lines.append(
                    f"Backfill: would process {tool} {session_dir.name} ({start} → {end}) using {copied_jsonl.name}"
                )
                processed_runs += 1
                processed_local += 1
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
                evaluator=evaluator_value,
                evaluator_model=model,
                claude_max_thinking_tokens=claude_tokens,
                continuation_of_run_id=prev_run_id,
            )
            processed_runs += 1
            processed_local += 1
            if not appended:
                output_lines.append(f"Backfill: failed run_id={run_id} ({session_dir.name})")
            else:
                output_lines.append(f"Backfill: appended run_id={run_id} ({session_dir.name})")
            prev_run_id = run_id

        return processed_local, halted_local, output_lines

    if max_concurrency_value > 1:
        session_jobs: list[tuple[str, Path]] = []
        for base in bases:
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
                session_jobs.append((tool_guess, session_dir))

        processed = 0
        completed_sessions = 0
        processed_runs = 0
        progress_started = time.monotonic()
        progress_stop = threading.Event()
        progress_thread: threading.Thread | None = None
        stop_event.clear()

        futures = []
        with ThreadPoolExecutor(max_workers=max_concurrency_value) as ex:
            futures = [ex.submit(_run_one_session, tool_guess, session_dir) for tool_guess, session_dir in session_jobs]
            if progress_enabled and session_jobs:
                progress_thread = threading.Thread(target=_progress_worker, args=(len(session_jobs),), daemon=True)
                progress_thread.start()
            for fut in as_completed(futures):
                proc_count, halted_local, lines = fut.result()
                with progress_lock:
                    completed_sessions += 1
                    processed_runs += proc_count
                processed += proc_count
                _progress_clear()
                for line in lines:
                    click.echo(line)
                if halted_local:
                    halted = True
                    for f in futures:
                        if not f.done():
                            f.cancel()
                    break

        if progress_thread is not None:
            progress_stop.set()
            progress_thread.join(timeout=2)
        _progress_clear()

        if halted:
            click.echo(f"Backfill halted: processed {processed} run(s).")
        else:
            click.echo(f"Backfill complete: processed {processed} run(s).")
        return

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
                        codex_resume_id=codex_resume_id
                        or (legacy_meta.get("codex_resume_id") if legacy_meta else None),
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
                        f"Backfill: would process {tool} {session_dir.name} ({start} → {end}) using {copied_jsonl.name}"
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
                    evaluator=evaluator_value,
                    evaluator_model=model,
                    claude_max_thinking_tokens=claude_tokens,
                    continuation_of_run_id=prev_run_id,
                )
                processed += 1

                if appended and status == "appended":
                    click.echo(f"Backfill: appended run_id={run_id} ({session_dir.name})")
                else:
                    click.echo(f"Backfill: failed run_id={run_id} ({session_dir.name})")

                prev_run_id = run_id

        if limit is not None and processed >= limit:
            break

    if halted:
        click.echo(f"Backfill halted: processed {processed} run(s).")
    else:
        click.echo(f"Backfill complete: processed {processed} run(s).")


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
@click.option("--org-uuid", help="Organization UUID (auto-detected from ~/.claude.json)")
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
            raise click.ClickException(f"API request failed: {e.response.status_code} {e.response.text}")
        except httpx.RequestError as e:
            raise click.ClickException(f"Request failed: {e}")

        if not sessions_data:
            click.echo("No sessions found.")
            return

        # Build choices for questionary
        choices = []
        for sess in sessions_data:
            display = format_session_for_display(sess)
            sid = sess.get("id")
            if sid:
                choices.append(questionary.Choice(title=display, value=sid))

        selected = questionary.select(
            "Select a session to convert:",
            choices=choices,
        ).ask()

        if selected is None:
            click.echo("No session selected.")
            return

        session_id = selected

    click.echo(f"Fetching session {session_id}...")
    session_data = fetch_session(token, org_uuid, session_id)

    # Determine output directory and whether to open browser
    auto_open = output is None and not gist and not output_auto
    if output_auto:
        parent_dir = Path(output) if output else Path(".")
        output = parent_dir / session_id
    elif output is None:
        output = Path(tempfile.gettempdir()) / f"claude-session-{session_id}"

    output = Path(output)
    generate_html_from_session_data(session_data, output, github_repo=repo)

    # Show output directory
    click.echo(f"Output: {output.resolve()}")

    # Copy JSON file to output directory if requested
    if include_json:
        output.mkdir(exist_ok=True)
        json_dest = output / f"{session_id}.json"
        json_dest.write_text(json.dumps(session_data, indent=2, ensure_ascii=False), encoding="utf-8")
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
    "--source",
    type=click.Path(),
    help="Claude Code projects directory (defaults to ~/.claude/projects).",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(),
    required=True,
    help="Output directory for the archive.",
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
                click.echo(f"\n  {project['name']} ({len(project['sessions'])} sessions)")
                for session in project["sessions"][:3]:  # Show first 3
                    mod_time = datetime.fromtimestamp(session["mtime"])
                    click.echo(f"    - {session['path'].stem} ({mod_time.strftime('%Y-%m-%d')})")
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

    if stats["failed_sessions"]:
        click.echo(f"\nWarning: {len(stats['failed_sessions'])} session(s) failed:")
        for failure in stats["failed_sessions"]:
            click.echo(f"  {failure['project']}/{failure['session']}: {failure['error']}")

    if not quiet:
        click.echo(f"\nGenerated archive with {stats['total_projects']} projects, {stats['total_sessions']} sessions")
        click.echo(f"Output: {output.resolve()}")

    if open_browser:
        index_url = (output / "index.html").resolve().as_uri()
        webbrowser.open(index_url)


def main():
    cli()
