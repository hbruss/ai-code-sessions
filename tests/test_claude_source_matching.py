from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import ai_code_sessions.core as core


def _touch_with_mtime(path: Path, *, mtime_dt: datetime) -> None:
    ts = mtime_dt.timestamp()
    os.utime(path, (ts, ts))


def test_claude_session_times_handles_file_history_snapshot(tmp_path: Path):
    session = tmp_path / "session.jsonl"
    session.write_text(
        "\n".join(
            [
                '{"type":"file-history-snapshot","snapshot":{"timestamp":"2026-01-05T00:46:41.473Z"}}',
                '{"type":"user","timestamp":"2026-01-05T00:46:41.462Z","cwd":"/p","sessionId":"s1","message":{"role":"user","content":"hi"}}',
                '{"type":"assistant","timestamp":"2026-01-05T01:52:53.485Z","cwd":"/p","sessionId":"s1","message":{"role":"assistant","content":[{"type":"text","text":"ok"}]}}',
                "",
            ]
        ),
        encoding="utf-8",
    )

    start_dt, end_dt, cwd, session_id = core._claude_session_times(session)

    assert start_dt == datetime(2026, 1, 5, 0, 46, 41, 473000, tzinfo=timezone.utc)
    assert end_dt == datetime(2026, 1, 5, 1, 52, 53, 485000, tzinfo=timezone.utc)
    assert cwd == "/p"
    assert session_id == "s1"


def test_find_best_source_file_prefers_long_claude_session_over_short(tmp_path: Path, monkeypatch):
    fake_home = tmp_path / "home"
    projects_dir = fake_home / ".claude" / "projects"
    project_root = "/home/user/projects/project-a"
    encoded = core._encode_claude_project_folder(project_root)
    project_dir = projects_dir / encoded
    project_dir.mkdir(parents=True)

    start = "2026-01-05T00:44:25.395Z"
    end = "2026-01-05T01:52:53.818Z"

    short = project_dir / "short.jsonl"
    short.write_text(
        "\n".join(
            [
                '{"type":"queue-operation","operation":"dequeue","timestamp":"2026-01-05T00:46:48.212Z","sessionId":"short"}',
                '{"type":"user","timestamp":"2026-01-05T00:46:48.222Z","cwd":"/home/user/projects/project-a","sessionId":"short","message":{"role":"user","content":"hi"}}',
                '{"type":"assistant","timestamp":"2026-01-05T00:46:49.931Z","cwd":"/home/user/projects/project-a","sessionId":"short","message":{"role":"assistant","content":[{"type":"text","text":"ok"}]}}',
                "",
            ]
        ),
        encoding="utf-8",
    )
    _touch_with_mtime(short, mtime_dt=datetime(2026, 1, 5, 0, 46, 49, 943723, tzinfo=timezone.utc))

    long = project_dir / "long.jsonl"
    long.write_text(
        "\n".join(
            [
                '{"type":"file-history-snapshot","snapshot":{"timestamp":"2026-01-05T00:46:41.473Z"},"isSnapshotUpdate":false}',
                '{"type":"user","timestamp":"2026-01-05T00:46:41.462Z","cwd":"/home/user/projects/project-a","sessionId":"long","message":{"role":"user","content":"do work"}}',
                '{"type":"assistant","timestamp":"2026-01-05T01:52:53.485Z","cwd":"/home/user/projects/project-a","sessionId":"long","message":{"role":"assistant","content":[{"type":"text","text":"done"}]}}',
                "",
            ]
        ),
        encoding="utf-8",
    )
    _touch_with_mtime(long, mtime_dt=datetime(2026, 1, 5, 1, 52, 53, 553843, tzinfo=timezone.utc))

    monkeypatch.setattr(core.Path, "home", lambda: fake_home)

    match = core.find_best_source_file(
        tool="claude",
        cwd=project_root,
        project_root=project_root,
        start=start,
        end=end,
    )

    assert match["best"]["path"].endswith("long.jsonl")

