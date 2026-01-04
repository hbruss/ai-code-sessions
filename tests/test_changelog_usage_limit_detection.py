import ai_code_sessions


def test_usage_limit_detection_ignores_rate_limits_key():
    assert ai_code_sessions._looks_like_usage_limit_error('{"rate_limits": {"primary": {"used_percent": 12.0}}}') is False


def test_usage_limit_detection_catches_http_429():
    assert ai_code_sessions._looks_like_usage_limit_error("HTTP 429 Too Many Requests") is True


def test_usage_limit_detection_catches_bare_429():
    assert ai_code_sessions._looks_like_usage_limit_error("error=429") is True
