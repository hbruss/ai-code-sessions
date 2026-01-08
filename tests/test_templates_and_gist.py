"""Tests for template rendering variants and gist helpers."""

import subprocess

import click

import ai_code_sessions.core as core


def test_repo_archive_template_renders_sessions():
    template = core.get_template("repo_archive.html")
    html = template.render(
        transcript_title="Archive",
        session_label=None,
        sessions=[
            {"label": "Run 1", "tool": "codex", "date": "2026-01-01", "duration": "1m", "pages": 1, "link": "x"},
        ],
        session_count=1,
        css="body{}",
        js="console.log('x')",
    )

    assert "Archive" in html
    assert "Run 1" in html


def test_inject_gist_preview_js(tmp_path):
    html_path = tmp_path / "index.html"
    html_path.write_text("<html><body>Hi</body></html>", encoding="utf-8")

    core.inject_gist_preview_js(tmp_path)

    content = html_path.read_text(encoding="utf-8")
    assert core.GIST_PREVIEW_JS in content


def test_create_gist_no_html_files(tmp_path):
    try:
        core.create_gist(tmp_path)
    except click.ClickException as exc:
        assert "No HTML files found" in str(exc)
    else:
        raise AssertionError("Expected ClickException")


def test_create_gist_success(monkeypatch, tmp_path):
    html_path = tmp_path / "index.html"
    html_path.write_text("<html></html>", encoding="utf-8")

    def fake_run(*_args, **_kwargs):
        return subprocess.CompletedProcess(
            args=["gh"],
            returncode=0,
            stdout="https://gist.github.com/user/abc123\n",
            stderr="",
        )

    monkeypatch.setattr(core.subprocess, "run", fake_run)

    gist_id, gist_url = core.create_gist(tmp_path)
    assert gist_id == "abc123"
    assert gist_url.endswith("/abc123")
