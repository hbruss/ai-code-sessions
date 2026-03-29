"""Tests for CLI setup/web/backfill/export-latest flows."""

import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import zipfile

import click
from click.testing import CliRunner
import pytest

import importlib

cli_module = importlib.import_module("ai_code_sessions.cli")
core_module = importlib.import_module("ai_code_sessions.core")
cli = cli_module.cli


class _Answer:
    def __init__(self, value):
        self._value = value

    def ask(self):
        return self._value


def test_changelog_sync_help_lists_expected_options():
    runner = CliRunner()
    result = runner.invoke(cli, ["changelog", "sync", "--help"])

    assert result.exit_code == 0
    assert "--codex" in result.output
    assert "--claude" in result.output
    assert "--all" in result.output
    assert "--since" in result.output
    assert "--until" in result.output
    assert "--limit" in result.output
    assert "--project-root" in result.output
    assert "--dry-run" in result.output


def test_setup_writes_configs_and_gitignore(monkeypatch, tmp_path):
    packaged_skill = tmp_path / "packaged-skill" / "changelog"
    packaged_skill.mkdir(parents=True)
    (packaged_skill / "SKILL.md").write_text("skill", encoding="utf-8")
    (packaged_skill / "changelog_utils.py").write_text("print('ok')\n", encoding="utf-8")
    (packaged_skill / "prime-session.sh").write_text("#!/bin/sh\n", encoding="utf-8")

    answers = iter(
        [
            "both",  # wrapped cli(s)
            True,  # changelog enabled
            "codex",  # evaluator
            "",  # model
            "alice",  # actor
            "America/Los_Angeles",  # tz
            False,  # commit changelog
        ]
    )

    monkeypatch.setattr(cli_module.questionary, "text", lambda *a, **k: _Answer(next(answers)))
    monkeypatch.setattr(cli_module.questionary, "confirm", lambda *a, **k: _Answer(next(answers)))
    monkeypatch.setattr(cli_module.questionary, "select", lambda *a, **k: _Answer(next(answers)))

    global_path = tmp_path / "global.toml"
    monkeypatch.setattr(cli_module, "_global_config_path", lambda: global_path)
    captured_cfg = {}

    def fake_render_config_toml(cfg):
        captured_cfg.update(cfg)
        return "cfg"

    monkeypatch.setattr(cli_module, "_render_config_toml", fake_render_config_toml)
    captured = {}

    def fake_ensure_gitignore(root, entry):
        captured["root"] = root
        captured["entry"] = entry

    monkeypatch.setattr(cli_module, "_ensure_gitignore_ignores", fake_ensure_gitignore)
    monkeypatch.setattr(cli_module, "_packaged_skill_path", lambda _name: packaged_skill)
    monkeypatch.setattr(cli_module, "_probe_cli_command", lambda command: (True, f"/resolved/{command}"))
    monkeypatch.setattr(
        cli_module.shutil,
        "which",
        lambda name: {
            "codex": "/usr/local/bin/codex",
            "claude": "/usr/local/bin/claude",
            "jq": "/usr/local/bin/jq",
            "rg": "/usr/local/bin/rg",
            "fd": "/usr/local/bin/fd",
            "gh": "/usr/local/bin/gh",
        }.get(name),
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "setup",
            "--project-root",
            str(tmp_path),
            "--global",
            "--repo",
            "--force",
        ],
    )

    assert result.exit_code == 0
    assert global_path.exists()
    assert (tmp_path / ".ai-code-sessions.toml").exists()
    assert captured["entry"] == ".changelog/"
    assert captured_cfg == {
        "ctx": {"tz": "America/Los_Angeles"},
        "changelog": {"enabled": True, "actor": "alice", "evaluator": "codex"},
    }
    assert "PASS" in result.output
    assert "codex" in result.output
    assert "claude" in result.output
    assert str(packaged_skill) in result.output
    assert "user-wide Codex" in result.output
    assert "project-local Codex" in result.output
    assert "user-wide Claude" in result.output
    assert "project-local Claude" in result.output
    assert f"cp -R {packaged_skill}/." in result.output


def test_setup_reports_missing_required_tools_for_selected_workflow(monkeypatch, tmp_path):
    packaged_skill = tmp_path / "packaged-skill" / "changelog"
    packaged_skill.mkdir(parents=True)
    (packaged_skill / "SKILL.md").write_text("skill", encoding="utf-8")
    (packaged_skill / "changelog_utils.py").write_text("print('ok')\n", encoding="utf-8")
    (packaged_skill / "prime-session.sh").write_text("#!/bin/sh\n", encoding="utf-8")

    answers = iter(
        [
            "codex",  # wrapped cli(s)
            True,  # changelog enabled
            "claude",  # evaluator
            "",  # claude model
            "8192",  # claude thinking tokens
            "alice",  # actor
            "America/Los_Angeles",  # tz
            False,  # commit changelog
        ]
    )

    monkeypatch.setattr(cli_module.questionary, "text", lambda *a, **k: _Answer(next(answers)))
    monkeypatch.setattr(cli_module.questionary, "confirm", lambda *a, **k: _Answer(next(answers)))
    monkeypatch.setattr(cli_module.questionary, "select", lambda *a, **k: _Answer(next(answers)))
    monkeypatch.delenv("CTX_CODEX_CMD", raising=False)
    monkeypatch.delenv("CTX_CLAUDE_CMD", raising=False)
    monkeypatch.setattr(cli_module, "_packaged_skill_path", lambda _name: packaged_skill)
    monkeypatch.setattr(cli_module, "_global_config_path", lambda: tmp_path / "global.toml")
    captured_cfg = {}

    def fake_render_config_toml(cfg):
        captured_cfg.update(cfg)
        return "cfg"

    monkeypatch.setattr(cli_module, "_render_config_toml", fake_render_config_toml)
    monkeypatch.setattr(cli_module, "_ensure_gitignore_ignores", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli_module, "_probe_cli_command", lambda command: (True, f"/resolved/{command}"))
    monkeypatch.setattr(
        cli_module.shutil,
        "which",
        lambda name: {
            "codex": "/usr/local/bin/codex",
            "claude": "/usr/local/bin/claude",
            "fd": "/usr/local/bin/fd",
        }.get(name),
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "setup",
            "--project-root",
            str(tmp_path),
            "--global",
            "--repo",
            "--force",
        ],
    )

    assert result.exit_code == 0
    assert "WARN jq: missing; packaged changelog skill helper scripts will be limited without jq" in result.output
    assert "WARN rg: missing; packaged changelog skill helper scripts will be limited without rg" in result.output
    assert "WARN" in result.output
    assert "user-wide Codex" in result.output
    assert "user-wide Claude" in result.output
    assert captured_cfg == {
        "ctx": {"tz": "America/Los_Angeles"},
        "changelog": {
            "enabled": True,
            "actor": "alice",
            "evaluator": "claude",
            "claude_thinking_tokens": 8192,
        },
    }


def test_setup_reports_helper_tool_limits_even_without_changelog(monkeypatch, tmp_path):
    packaged_skill = tmp_path / "packaged-skill" / "changelog"
    packaged_skill.mkdir(parents=True)
    (packaged_skill / "SKILL.md").write_text("skill", encoding="utf-8")
    (packaged_skill / "changelog_utils.py").write_text("print('ok')\n", encoding="utf-8")
    (packaged_skill / "prime-session.sh").write_text("#!/bin/sh\n", encoding="utf-8")

    answers = iter(
        [
            "codex",  # wrapped cli(s)
            False,  # changelog enabled
            "alice",  # actor
            "America/Los_Angeles",  # tz
            True,  # write global config?
            True,  # write repo config?
            False,  # commit changelog
        ]
    )

    monkeypatch.setattr(cli_module.questionary, "text", lambda *a, **k: _Answer(next(answers)))
    monkeypatch.setattr(cli_module.questionary, "confirm", lambda *a, **k: _Answer(next(answers)))
    monkeypatch.setattr(cli_module.questionary, "select", lambda *a, **k: _Answer(next(answers)))
    monkeypatch.setattr(cli_module, "_packaged_skill_path", lambda _name: packaged_skill)
    monkeypatch.setattr(cli_module, "_probe_cli_command", lambda command: (True, f"/resolved/{command}"))
    monkeypatch.setattr(cli_module, "_global_config_path", lambda: tmp_path / "global.toml")
    monkeypatch.setattr(cli_module, "_ensure_gitignore_ignores", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli_module.shutil,
        "which",
        lambda name: {
            "codex": "/usr/local/bin/codex",
        }.get(name),
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "setup",
            "--project-root",
            str(tmp_path),
            "--force",
        ],
    )

    assert result.exit_code == 0
    assert "WARN jq: missing; packaged changelog skill helper scripts will be limited without jq" in result.output
    assert "WARN rg: missing; packaged changelog skill helper scripts will be limited without rg" in result.output


def test_setup_asks_config_scope_when_flags_not_explicit(monkeypatch, tmp_path):
    packaged_skill = tmp_path / "packaged-skill" / "changelog"
    packaged_skill.mkdir(parents=True)
    (packaged_skill / "SKILL.md").write_text("skill", encoding="utf-8")
    (packaged_skill / "changelog_utils.py").write_text("print('ok')\n", encoding="utf-8")
    (packaged_skill / "prime-session.sh").write_text("#!/bin/sh\n", encoding="utf-8")

    answers = iter(
        [
            "codex",  # wrapped cli(s)
            False,  # changelog enabled
            "alice",  # actor
            "America/Los_Angeles",  # tz
            False,  # write global config?
            True,  # write repo config?
            False,  # commit changelog
        ]
    )

    monkeypatch.setattr(cli_module.questionary, "text", lambda *a, **k: _Answer(next(answers)))
    monkeypatch.setattr(cli_module.questionary, "confirm", lambda *a, **k: _Answer(next(answers)))
    monkeypatch.setattr(cli_module.questionary, "select", lambda *a, **k: _Answer(next(answers)))
    monkeypatch.setattr(cli_module, "_packaged_skill_path", lambda _name: packaged_skill)
    monkeypatch.setattr(cli_module, "_probe_cli_command", lambda command: (True, f"/resolved/{command}"))
    monkeypatch.setattr(cli_module, "_global_config_path", lambda: tmp_path / "global.toml")
    monkeypatch.setattr(cli_module, "_ensure_gitignore_ignores", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli_module.shutil,
        "which",
        lambda name: {
            "codex": "/usr/local/bin/codex",
            "jq": "/usr/local/bin/jq",
            "rg": "/usr/local/bin/rg",
        }.get(name),
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "setup",
            "--project-root",
            str(tmp_path),
            "--force",
        ],
    )

    assert result.exit_code == 0
    assert not (tmp_path / "global.toml").exists()
    assert (tmp_path / ".ai-code-sessions.toml").exists()


def test_setup_preflight_respects_existing_custom_cli_commands(monkeypatch, tmp_path):
    packaged_skill = tmp_path / "packaged-skill" / "changelog"
    packaged_skill.mkdir(parents=True)
    (packaged_skill / "SKILL.md").write_text("skill", encoding="utf-8")
    (packaged_skill / "changelog_utils.py").write_text("print('ok')\n", encoding="utf-8")
    (packaged_skill / "prime-session.sh").write_text("#!/bin/sh\n", encoding="utf-8")

    answers = iter(
        [
            "both",  # wrapped cli(s)
            False,  # changelog enabled
            "alice",  # actor
            "America/Los_Angeles",  # tz
            False,  # write global config?
            False,  # write repo config?
            False,  # commit changelog
        ]
    )

    monkeypatch.setattr(cli_module.questionary, "text", lambda *a, **k: _Answer(next(answers)))
    monkeypatch.setattr(cli_module.questionary, "confirm", lambda *a, **k: _Answer(next(answers)))
    monkeypatch.setattr(cli_module.questionary, "select", lambda *a, **k: _Answer(next(answers)))
    monkeypatch.delenv("CTX_CODEX_CMD", raising=False)
    monkeypatch.delenv("CTX_CLAUDE_CMD", raising=False)
    monkeypatch.setattr(cli_module, "_packaged_skill_path", lambda _name: packaged_skill)
    monkeypatch.setattr(
        cli_module,
        "_load_config",
        lambda **_kwargs: {
            "ctx": {
                "codex_cmd": "/opt/custom/codex-real",
                "claude_cmd": "/opt/custom/claude-real",
            }
        },
    )
    probed = []

    def fake_probe(command):
        probed.append(command)
        return True, f"/resolved/{Path(command).name}"

    monkeypatch.setattr(cli_module, "_probe_cli_command", fake_probe)
    monkeypatch.setattr(
        cli_module.shutil,
        "which",
        lambda name: {
            "jq": "/usr/local/bin/jq",
            "rg": "/usr/local/bin/rg",
        }.get(name),
    )
    monkeypatch.setattr(cli_module, "_ensure_gitignore_ignores", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli_module, "_global_config_path", lambda: tmp_path / "global.toml")

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "setup",
            "--project-root",
            str(tmp_path),
            "--force",
        ],
    )

    assert result.exit_code == 0
    assert "/opt/custom/codex-real" in probed
    assert "/opt/custom/claude-real" in probed


def test_setup_prints_windows_friendly_skill_install_guidance(monkeypatch, tmp_path):
    packaged_skill = tmp_path / "packaged-skill" / "changelog"
    packaged_skill.mkdir(parents=True)
    (packaged_skill / "SKILL.md").write_text("skill", encoding="utf-8")
    (packaged_skill / "changelog_utils.py").write_text("print('ok')\n", encoding="utf-8")
    (packaged_skill / "prime-session.sh").write_text("#!/bin/sh\n", encoding="utf-8")

    answers = iter(
        [
            "codex",  # wrapped cli(s)
            False,  # changelog enabled
            "alice",  # actor
            "America/Los_Angeles",  # tz
            False,  # write global config?
            False,  # write repo config?
            False,  # commit changelog
        ]
    )

    monkeypatch.setattr(cli_module.questionary, "text", lambda *a, **k: _Answer(next(answers)))
    monkeypatch.setattr(cli_module.questionary, "confirm", lambda *a, **k: _Answer(next(answers)))
    monkeypatch.setattr(cli_module.questionary, "select", lambda *a, **k: _Answer(next(answers)))
    monkeypatch.setattr(cli_module, "_packaged_skill_path", lambda _name: packaged_skill)
    monkeypatch.setattr(cli_module, "_probe_cli_command", lambda command: (True, f"/resolved/{command}"))
    monkeypatch.setattr(cli_module, "_ensure_gitignore_ignores", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        cli_module.shutil,
        "which",
        lambda name: {
            "codex": "/usr/local/bin/codex",
            "jq": "/usr/local/bin/jq",
            "rg": "/usr/local/bin/rg",
        }.get(name),
    )
    monkeypatch.setattr(cli_module.sys, "platform", "win32", raising=False)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "setup",
            "--project-root",
            str(tmp_path),
            "--force",
        ],
    )

    assert result.exit_code == 0
    assert "Manual skill install (PowerShell)" in result.output
    assert "Copy-Item -Recurse -Force" in result.output
    assert "Test-Path" in result.output
    assert "prime-session.sh is a POSIX shell helper" in result.output
    assert "cp -R" not in result.output


def test_packaged_changelog_skill_bundle_exists():
    skill_path = cli_module._packaged_skill_path("changelog")

    assert skill_path == Path(skill_path)
    assert skill_path.is_dir()
    assert (skill_path / "SKILL.md").exists()
    assert (skill_path / "changelog_utils.py").exists()
    assert (skill_path / "prime-session.sh").exists()


class _FakeTraversable:
    def __init__(self, name, *, children=None, content=None):
        self.name = name
        self._children = children
        self._content = content

    def is_dir(self):
        return self._children is not None

    def is_file(self):
        return self._children is None

    def iterdir(self):
        if not self.is_dir():
            raise FileNotFoundError(self.name)
        return iter(self._children.values())

    def joinpath(self, *descendants):
        node = self
        for descendant in descendants:
            if not node.is_dir():
                raise FileNotFoundError(descendant)
            node = node._children[descendant]
        return node

    def open(self, mode="r", *args, **kwargs):
        if self.is_dir():
            raise IsADirectoryError(self.name)
        payload = self._content or b""
        if "b" in mode:
            return io.BytesIO(payload)
        encoding = kwargs.get("encoding", "utf-8")
        return io.StringIO(payload.decode(encoding))

    def __str__(self):
        return f"<non-filesystem:{self.name}>"


def test_packaged_skill_path_materializes_non_filesystem_resources(monkeypatch, tmp_path):
    fake_tree = _FakeTraversable(
        "root",
        children={
            "skills": _FakeTraversable(
                "skills",
                children={
                    "changelog": _FakeTraversable(
                        "changelog",
                        children={
                            "SKILL.md": _FakeTraversable("SKILL.md", content=b"skill"),
                            "changelog_utils.py": _FakeTraversable(
                                "changelog_utils.py",
                                content=b"print('utils')\n",
                            ),
                            "prime-session.sh": _FakeTraversable("prime-session.sh", content=b"#!/bin/sh\n"),
                        },
                    )
                },
            )
        },
    )
    monkeypatch.setattr(core_module.importlib_resources, "files", lambda _package: fake_tree)
    monkeypatch.setattr(core_module.tempfile, "gettempdir", lambda: str(tmp_path))

    skill_path = core_module._packaged_skill_path("changelog")

    assert skill_path.is_dir()
    assert (skill_path / "SKILL.md").read_text(encoding="utf-8") == "skill"
    assert (skill_path / "changelog_utils.py").read_text(encoding="utf-8") == "print('utils')\n"
    assert (skill_path / "prime-session.sh").read_text(encoding="utf-8") == "#!/bin/sh\n"


def test_built_artifacts_include_packaged_changelog_skill_files(tmp_path):
    if not shutil.which("uv"):
        pytest.skip("uv is required for build verification")

    repo_root = Path(__file__).resolve().parents[1]
    build_dir = tmp_path / "dist"
    env = os.environ.copy()
    env["UV_CACHE_DIR"] = str(tmp_path / "uv-cache")

    result = subprocess.run(
        ["uv", "build", "--out-dir", str(build_dir), "--clear"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr

    wheel_path = next(build_dir.glob("*.whl"))
    with zipfile.ZipFile(wheel_path) as wheel:
        wheel_names = set(wheel.namelist())
    assert "ai_code_sessions/skills/changelog/SKILL.md" in wheel_names
    assert "ai_code_sessions/skills/changelog/changelog_utils.py" in wheel_names
    assert "ai_code_sessions/skills/changelog/prime-session.sh" in wheel_names

    sdist_path = next(build_dir.glob("*.tar.gz"))
    with tarfile.open(sdist_path, "r:gz") as sdist:
        sdist_names = set(sdist.getnames())
    assert any(name.endswith("/src/ai_code_sessions/skills/changelog/SKILL.md") for name in sdist_names)
    assert any(name.endswith("/src/ai_code_sessions/skills/changelog/changelog_utils.py") for name in sdist_names)
    assert any(name.endswith("/src/ai_code_sessions/skills/changelog/prime-session.sh") for name in sdist_names)


def test_web_happy_path_writes_json(monkeypatch, tmp_path):
    session_data = {
        "loglines": [
            {
                "type": "user",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {"role": "user", "content": "Hello"},
            }
        ]
    }

    monkeypatch.setattr(cli_module, "resolve_credentials", lambda *_: ("tok", "org"))
    monkeypatch.setattr(cli_module, "fetch_session", lambda *_: session_data)

    def fake_generate(session, output, **_kwargs):
        output.mkdir(parents=True, exist_ok=True)
        (output / "index.html").write_text("<html></html>", encoding="utf-8")

    monkeypatch.setattr(cli_module, "generate_html_from_session_data", fake_generate)

    output_dir = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "web",
            "sess-1",
            "--token",
            "tok",
            "--org-uuid",
            "org",
            "--output",
            str(output_dir),
            "--json",
        ],
    )

    assert result.exit_code == 0
    assert (output_dir / "index.html").exists()
    assert (output_dir / "sess-1.json").exists()


def test_web_interactive_repo_filtering(monkeypatch, tmp_path):
    monkeypatch.setattr(cli_module, "resolve_credentials", lambda *_: ("tok", "org"))

    sessions_data = {
        "data": [
            {
                "id": "sess-1",
                "title": "Session 1",
                "created_at": "2025-01-01T10:00:00.000Z",
                "session_context": {
                    "outcomes": [
                        {"type": "git_repository", "git_info": {"repo": "acme/widgets", "type": "github"}},
                    ]
                },
            },
            {
                "id": "sess-2",
                "title": "Session 2",
                "created_at": "2025-01-02T10:00:00.000Z",
                "session_context": {
                    "outcomes": [
                        {"type": "git_repository", "git_info": {"repo": "other/repo", "type": "github"}},
                    ]
                },
            },
        ]
    }
    monkeypatch.setattr(cli_module, "fetch_sessions", lambda *_: sessions_data)

    session_data = {
        "loglines": [
            {"type": "user", "timestamp": "2026-01-01T00:00:00Z", "message": {"role": "user", "content": "Hello"}}
        ]
    }
    monkeypatch.setattr(cli_module, "fetch_session", lambda *_: session_data)

    def fake_generate(_session, output, **_kwargs):
        output.mkdir(parents=True, exist_ok=True)
        (output / "index.html").write_text("<html></html>", encoding="utf-8")

    monkeypatch.setattr(cli_module, "generate_html_from_session_data", fake_generate)

    captured = {}

    class MockSelect:
        def __init__(self, *_args, **kwargs):
            captured["choices"] = kwargs.get("choices") or []

        def ask(self):
            return "sess-1"

    monkeypatch.setattr(cli_module.questionary, "select", MockSelect)

    output_dir = tmp_path / "out"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "web",
            "--repo",
            "acme/widgets",
            "--token",
            "tok",
            "--org-uuid",
            "org",
            "--output",
            str(output_dir),
        ],
    )

    assert result.exit_code == 0
    assert (output_dir / "index.html").exists()
    assert len(captured["choices"]) == 1


def test_web_interactive_repo_filtering_no_matches(monkeypatch):
    monkeypatch.setattr(cli_module, "resolve_credentials", lambda *_: ("tok", "org"))
    monkeypatch.setattr(
        cli_module,
        "fetch_sessions",
        lambda *_: {
            "data": [
                {
                    "id": "sess-1",
                    "title": "Session 1",
                    "created_at": "2025-01-01T10:00:00.000Z",
                    "session_context": {
                        "outcomes": [
                            {"type": "git_repository", "git_info": {"repo": "other/repo", "type": "github"}},
                        ]
                    },
                }
            ]
        },
    )

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "web",
            "--repo",
            "acme/widgets",
            "--token",
            "tok",
            "--org-uuid",
            "org",
        ],
    )

    assert result.exit_code != 0
    assert "No sessions found for repo" in result.output


def test_backfill_sequential_appends(monkeypatch, tmp_path):
    session_dir = tmp_path / ".codex" / "sessions" / "2026-01-01-0000_test"
    session_dir.mkdir(parents=True)

    copied_jsonl = session_dir / "rollout-abc.jsonl"
    copied_jsonl.write_text("{}", encoding="utf-8")

    export_runs = session_dir / "export_runs.jsonl"
    export_runs.write_text(
        json.dumps(
            {
                "start": "2026-01-01T00:00:00+00:00",
                "end": "2026-01-01T01:00:00+00:00",
                "tool": "codex",
                "copied_jsonl": str(copied_jsonl),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    def fake_append(**_kwargs):
        return True, "run-123", "appended"

    monkeypatch.setattr(cli_module, "_generate_and_append_changelog_entry", fake_append)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "changelog",
            "backfill",
            "--project-root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "Backfill: appended" in result.output


def test_export_latest_find_best_source_error(monkeypatch, tmp_path):
    def fake_find_best_source_file(**_kwargs):
        raise click.ClickException("boom")

    monkeypatch.setattr(cli_module, "find_best_source_file", fake_find_best_source_file)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "export-latest",
            "--tool",
            "codex",
            "--cwd",
            str(tmp_path),
            "--project-root",
            str(tmp_path),
            "--start",
            "2026-01-01T00:00:00Z",
            "--end",
            "2026-01-01T01:00:00Z",
            "--output",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code != 0
    assert "boom" in result.output
