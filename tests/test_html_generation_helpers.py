"""Tests for HTML generation helpers and output management."""

from pathlib import Path

from ai_code_transcripts import (
    generate_html,
    generate_html_from_session_data,
    prepare_output_dir,
    prune_stale_pages,
)


def test_prepare_output_dir_overwrite_removes_html(tmp_path):
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (output_dir / "index.html").write_text("old", encoding="utf-8")
    (output_dir / "page-001.html").write_text("old", encoding="utf-8")
    (output_dir / "search_index.json").write_text("old", encoding="utf-8")
    (output_dir / "keep.txt").write_text("keep", encoding="utf-8")

    prepare_output_dir(output_dir=output_dir, mode="overwrite", project_root=tmp_path)

    assert not (output_dir / "index.html").exists()
    assert not (output_dir / "page-001.html").exists()
    assert not (output_dir / "search_index.json").exists()
    assert (output_dir / "keep.txt").exists()


def test_prepare_output_dir_clean_recreates(tmp_path):
    output_dir = tmp_path / "cleaned"
    output_dir.mkdir()
    (output_dir / "old.txt").write_text("old", encoding="utf-8")

    prepare_output_dir(output_dir=output_dir, mode="clean", project_root=tmp_path)

    assert output_dir.exists()
    assert not (output_dir / "old.txt").exists()


def test_prune_stale_pages_removes_excess(tmp_path):
    output_dir = tmp_path / "pages"
    output_dir.mkdir()
    (output_dir / "page-001.html").write_text("p1", encoding="utf-8")
    (output_dir / "page-002.html").write_text("p2", encoding="utf-8")
    (output_dir / "page-010.html").write_text("p10", encoding="utf-8")

    prune_stale_pages(output_dir=output_dir, total_pages=2)

    assert (output_dir / "page-001.html").exists()
    assert (output_dir / "page-002.html").exists()
    assert not (output_dir / "page-010.html").exists()


def test_generate_html_from_session_data_creates_files(tmp_path):
    session_data = {
        "source_format": "claude",
        "loglines": [
            {
                "type": "user",
                "timestamp": "2026-01-01T00:00:00Z",
                "message": {"role": "user", "content": "Hello"},
            },
            {
                "type": "assistant",
                "timestamp": "2026-01-01T00:00:01Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Hi"}],
                },
            },
        ],
    }

    output_dir = tmp_path / "session"
    generate_html_from_session_data(session_data, output_dir)

    assert (output_dir / "index.html").exists()
    assert (output_dir / "page-001.html").exists()


def test_generate_html_prunes_stale_pages(tmp_path):
    jsonl_path = tmp_path / "session.jsonl"
    jsonl_path.write_text(
        '{"type":"user","timestamp":"2026-01-01T00:00:00Z","message":{"role":"user","content":"Hello"}}\n'
        '{"type":"assistant","timestamp":"2026-01-01T00:00:01Z","message":{"role":"assistant","content":[{"type":"text","text":"Hi"}]}}\n',
        encoding="utf-8",
    )

    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "page-999.html").write_text("stale", encoding="utf-8")

    generate_html(jsonl_path, output_dir, prune_pages=True, project_root=tmp_path)

    assert not (output_dir / "page-999.html").exists()
