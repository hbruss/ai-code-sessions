import json
from pathlib import Path

import ai_code_sessions


def test_changelog_created_at_uses_session_start(monkeypatch, tmp_path: Path):
    project_root = tmp_path / "repo"
    project_root.mkdir()

    session_dir = project_root / ".codex" / "sessions" / "2025-10-01-0102_Test"
    session_dir.mkdir(parents=True)

    source_jsonl = tmp_path / "source.jsonl"
    source_jsonl.write_text("{}", encoding="utf-8")

    source_match_json = tmp_path / "source_match.json"
    source_match_json.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        ai_code_sessions,
        "_build_changelog_digest",
        lambda **_: {
            "delta": {
                "touched_files": {"created": [], "modified": [], "deleted": [], "moved": []},
                "tests": [],
                "commits": [],
            }
        },
    )

    monkeypatch.setattr(
        ai_code_sessions,
        "_run_codex_changelog_evaluator",
        lambda **_: {"summary": "ok", "bullets": ["did thing"], "tags": [], "notes": None},
    )

    start = "2025-10-01T01:02:03+00:00"
    end = "2025-10-01T02:03:04+00:00"

    ok, run_id, status = ai_code_sessions._generate_and_append_changelog_entry(
        tool="codex",
        label="Test",
        cwd=str(project_root),
        project_root=project_root,
        session_dir=session_dir,
        start=start,
        end=end,
        source_jsonl=source_jsonl,
        source_match_json=source_match_json,
        actor="hbruss",
        evaluator="codex",
        evaluator_model=None,
        claude_max_thinking_tokens=None,
        continuation_of_run_id=None,
        halt_on_429=False,
    )

    assert ok is True
    assert status == "appended"
    assert isinstance(run_id, str) and run_id

    entries_path = project_root / ".changelog" / "hbruss" / "entries.jsonl"
    entry = json.loads(entries_path.read_text(encoding="utf-8").strip().splitlines()[0])
    assert entry["created_at"] == start
