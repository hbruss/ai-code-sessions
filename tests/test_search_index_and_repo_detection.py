"""Tests for search index edge cases and repo detection fallbacks."""

import json

import ai_code_sessions.core as core


def test_detect_github_repo_from_meta_variants():
    assert (
        core._detect_github_repo_from_meta({"git": {"repositoryUrl": "git@github.com:owner/repo.git"}}) == "owner/repo"
    )
    assert core._detect_github_repo_from_meta({"git": {"repoUrl": "https://github.com/owner/repo.git"}}) == "owner/repo"
    assert core._detect_github_repo_from_meta({"repository_url": "https://github.com/owner/repo"}) == "owner/repo"


def test_detect_github_repo_from_tool_result():
    loglines = [
        {
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "content": "remote: Create a pull request for acme/widgets: https://github.com/acme/widgets/pull/new/feature",
                    }
                ]
            }
        }
    ]
    assert core.detect_github_repo(loglines) == "acme/widgets"


def test_build_search_index_tool_reply_role():
    message_json = json.dumps({"content": [{"type": "tool_result", "content": "ok"}]})
    conversations = [{"messages": [("user", message_json, "2026-01-01T00:00:00Z")]}]

    index = core._build_search_index(conversations=conversations, total_pages=1)

    assert index["items"][0]["role"] == "tool-reply"


def test_build_search_index_truncates_long_text():
    long_text = "x" * (core.SEARCH_INDEX_TEXT_MAX_CHARS + 200)
    message_json = json.dumps({"content": long_text})
    conversations = [{"messages": [("user", message_json, "2026-01-01T00:00:00Z")]}]

    index = core._build_search_index(conversations=conversations, total_pages=1)

    assert len(index["items"][0]["text"]) <= core.SEARCH_INDEX_TEXT_MAX_CHARS


def test_message_plain_text_handles_non_serializable_tool_input():
    message_data = {"content": [{"type": "tool_use", "name": "Weird", "input": {1, 2}}]}

    text = core._message_plain_text(message_data)

    assert "[tool_use:Weird]" in text
    assert "1" in text


def test_build_search_index_skips_image_only_blocks():
    message_json = json.dumps(
        {
            "content": [
                {
                    "type": "image",
                    "source": {"media_type": "image/png", "data": "abc"},
                }
            ]
        }
    )
    conversations = [{"messages": [("assistant", message_json, "2026-01-01T00:00:00Z")]}]

    index = core._build_search_index(conversations=conversations, total_pages=1)

    assert index["items"] == []


def test_build_search_index_mixed_user_role():
    message_json = json.dumps(
        {
            "content": [
                {"type": "text", "text": "Hello"},
                {"type": "tool_result", "content": "ok"},
            ]
        }
    )
    conversations = [{"messages": [("user", message_json, "2026-01-01T00:00:00Z")]}]

    index = core._build_search_index(conversations=conversations, total_pages=1)

    assert index["items"][0]["role"] == "user"
