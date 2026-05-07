"""Final greeting string assembly."""

from __future__ import annotations

from .greetings import lookup


def render_greeting(*, name: str, language: str, mood: str) -> str:
    """Format a greeting for the given (name, language, mood) triple."""
    template = lookup(language, mood)
    return template.format(name=name)
