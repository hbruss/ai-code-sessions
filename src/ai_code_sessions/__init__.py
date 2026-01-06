"""Convert Codex CLI and Claude Code session logs to HTML transcripts."""

import webbrowser as webbrowser  # noqa: F401

from . import core as _core
from .cli import cli

# Re-export core API (including underscore-prefixed helpers used by tests).
globals().update({k: v for k, v in _core.__dict__.items() if not k.startswith("__")})


def main():
    cli()
