"""Tests for changelog validation functions."""

from ai_code_sessions.core import (
    _looks_truncated,
    _parse_relative_date,
    _sanitize_changelog_text,
    _validate_changelog_entry,
)


class TestLooksTruncated:
    """Tests for _looks_truncated()."""

    def test_complete_sentence_with_period(self):
        assert _looks_truncated("This is a complete sentence.") is False

    def test_complete_sentence_with_exclamation(self):
        assert _looks_truncated("This is a complete sentence!") is False

    def test_complete_sentence_with_question(self):
        assert _looks_truncated("Is this a complete sentence?") is False

    def test_ends_with_closing_paren(self):
        assert _looks_truncated("Added tests (unit and integration)") is False

    def test_ends_with_closing_bracket(self):
        assert _looks_truncated("Updated config [production]") is False

    def test_ends_with_backtick(self):
        assert _looks_truncated("Fixed bug in `process_data`") is False

    def test_ends_with_single_quote(self):
        assert _looks_truncated("Updated the 'config'") is False

    def test_ends_with_double_quote(self):
        assert _looks_truncated('Set the value to "enabled"') is False

    def test_common_abbreviations(self):
        assert _looks_truncated("Added support for JSON, YAML, etc") is False
        assert _looks_truncated("Works with various backends, eg") is False
        assert _looks_truncated("Such as arrays, lists, ie") is False

    def test_truncated_lowercase_ending(self):
        assert _looks_truncated("Fixed the authenticat") is True
        assert _looks_truncated("Updated src/cla") is True

    def test_truncated_with_slash(self):
        assert _looks_truncated("Modified path/to/") is True

    def test_truncated_with_backslash(self):
        assert _looks_truncated("Fixed windows\\path\\") is True

    def test_truncated_with_open_paren(self):
        assert _looks_truncated("Called function(") is True

    def test_truncated_with_open_bracket(self):
        assert _looks_truncated("Updated array[") is True

    def test_truncated_with_comma(self):
        assert _looks_truncated("Added foo, bar,") is True

    def test_truncated_with_equals(self):
        assert _looks_truncated("Set value=") is True

    def test_empty_string(self):
        assert _looks_truncated("") is False

    def test_whitespace_only(self):
        assert _looks_truncated("   ") is False

    def test_none_handled_gracefully(self):
        # Although the function expects str, check edge case behavior
        assert _looks_truncated(None) is False  # type: ignore


class TestSanitizeChangelogText:
    """Tests for _sanitize_changelog_text()."""

    def test_plain_ascii(self):
        assert _sanitize_changelog_text("Hello, world!") == "Hello, world!"

    def test_removes_devanagari(self):
        # This was the actual garbage found in changelog entries
        assert _sanitize_changelog_text("features.ुध") == "features."

    def test_preserves_common_typographic_chars(self):
        text = "Added en-dash – and em-dash —"
        assert "–" in _sanitize_changelog_text(text)
        assert "—" in _sanitize_changelog_text(text)

    def test_preserves_smart_quotes(self):
        text = "Updated 'config' file"
        assert "'" in _sanitize_changelog_text(text)
        assert "'" in _sanitize_changelog_text(text)

    def test_preserves_bullets_and_dots(self):
        text = "• First item · Second item"
        assert "•" in _sanitize_changelog_text(text)
        assert "·" in _sanitize_changelog_text(text)

    def test_preserves_math_symbols(self):
        text = "Value ≥ 10 and ≤ 100"
        assert "≥" in _sanitize_changelog_text(text)
        assert "≤" in _sanitize_changelog_text(text)

    def test_preserves_non_english_and_emoji(self):
        text = "Added 日本語 🚀 support"
        sanitized = _sanitize_changelog_text(text)
        assert "日本語" in sanitized
        assert "🚀" in sanitized

    def test_empty_string(self):
        assert _sanitize_changelog_text("") == ""

    def test_none_handled_gracefully(self):
        assert _sanitize_changelog_text(None) == ""  # type: ignore


class TestParseRelativeDate:
    """Tests for _parse_relative_date()."""

    def test_yesterday(self):
        dt = _parse_relative_date("yesterday")
        assert dt is not None
        # Should be start of yesterday (00:00:00)
        assert dt.hour == 0
        assert dt.minute == 0
        assert dt.second == 0

    def test_today(self):
        dt = _parse_relative_date("today")
        assert dt is not None
        assert dt.hour == 0
        assert dt.minute == 0

    def test_n_days_ago(self):
        dt = _parse_relative_date("3 days ago")
        assert dt is not None

    def test_n_weeks_ago(self):
        dt = _parse_relative_date("2 weeks ago")
        assert dt is not None

    def test_n_hours_ago(self):
        dt = _parse_relative_date("5 hours ago")
        assert dt is not None

    def test_n_minutes_ago(self):
        dt = _parse_relative_date("30 minutes ago")
        assert dt is not None

    def test_n_months_ago(self):
        dt = _parse_relative_date("2 months ago")
        assert dt is not None

    def test_last_week(self):
        dt = _parse_relative_date("last week")
        assert dt is not None

    def test_last_month(self):
        dt = _parse_relative_date("last month")
        assert dt is not None

    def test_case_insensitive(self):
        assert _parse_relative_date("YESTERDAY") is not None
        assert _parse_relative_date("Today") is not None
        assert _parse_relative_date("3 DAYS AGO") is not None

    def test_invalid_string(self):
        assert _parse_relative_date("not a date") is None
        assert _parse_relative_date("abc123") is None

    def test_iso_date_not_parsed(self):
        # This function only handles relative dates, not ISO
        assert _parse_relative_date("2026-01-01") is None


class TestValidateChangelogEntry:
    """Tests for _validate_changelog_entry()."""

    def test_valid_entry(self):
        entry = {
            "summary": "Fixed authentication bug in login flow.",
            "bullets": [
                "Added mutex lock around token refresh.",
                "Implemented retry logic for failed requests.",
                "Updated unit tests for auth module.",
            ],
            "tags": ["fix", "auth"],
        }
        result = _validate_changelog_entry(entry)
        assert result.valid is True
        assert result.errors == []
        assert result.warnings == []

    def test_missing_summary(self):
        entry = {
            "bullets": ["Some bullet."],
            "tags": [],
        }
        result = _validate_changelog_entry(entry)
        assert result.valid is False
        assert any("summary" in e.lower() for e in result.errors)

    def test_empty_summary(self):
        entry = {
            "summary": "   ",
            "bullets": ["Some bullet."],
            "tags": [],
        }
        result = _validate_changelog_entry(entry)
        assert result.valid is False
        assert any("empty" in e.lower() for e in result.errors)

    def test_missing_bullets(self):
        entry = {
            "summary": "Valid summary here.",
            "tags": [],
        }
        result = _validate_changelog_entry(entry)
        assert result.valid is False
        assert any("bullet" in e.lower() for e in result.errors)

    def test_truncated_bullet_warning(self):
        entry = {
            "summary": "Valid summary.",
            "bullets": [
                "This bullet is truncat",  # Truncated mid-word
                "This one is complete.",
            ],
            "tags": [],
        }
        result = _validate_changelog_entry(entry)
        assert result.valid is True  # Warnings don't make it invalid
        assert any("truncated" in w.lower() for w in result.warnings)

    def test_unicode_garbage_warning(self):
        entry = {
            "summary": "Valid summary.",
            "bullets": [
                "Added features.ुध",  # Devanagari garbage
            ],
            "tags": [],
        }
        result = _validate_changelog_entry(entry)
        assert result.valid is True
        assert any("unicode" in w.lower() for w in result.warnings)

    def test_short_bullet_warning(self):
        entry = {
            "summary": "Valid summary.",
            "bullets": ["a."],  # Very short
            "tags": [],
        }
        result = _validate_changelog_entry(entry)
        assert result.valid is True
        assert any("short" in w.lower() for w in result.warnings)

    def test_path_only_bullet_warning(self):
        entry = {
            "summary": "Valid summary.",
            "bullets": ["src/ai_code_sessions/core.py"],  # Just a file path
            "tags": [],
        }
        result = _validate_changelog_entry(entry)
        assert result.valid is True
        assert any("file path" in w.lower() for w in result.warnings)

    def test_short_summary_warning(self):
        entry = {
            "summary": "Fix.",  # Too short
            "bullets": ["Added mutex lock around token refresh."],
            "tags": [],
        }
        result = _validate_changelog_entry(entry)
        assert result.valid is True
        assert any("short" in w.lower() for w in result.warnings)

    def test_non_string_bullet_error(self):
        entry = {
            "summary": "Valid summary.",
            "bullets": [123, "Valid bullet."],  # Number instead of string
            "tags": [],
        }
        result = _validate_changelog_entry(entry)
        assert result.valid is False
        assert any("not a string" in e.lower() for e in result.errors)
