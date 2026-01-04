"""Backwards-compatibility shim.

The implementation lives in `ai_code_sessions`.

This module exists so older imports (`import claude_code_transcripts`) keep working.
"""

import ai_code_sessions as _impl
import sys as _sys

_sys.modules[__name__] = _impl
