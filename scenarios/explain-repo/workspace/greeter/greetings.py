"""Translations for hello-world greetings, keyed by language and mood."""

from __future__ import annotations

GREETINGS: dict[str, dict[str, str]] = {
    "en": {
        "casual": "Hi {name}!",
        "formal": "Good day, {name}.",
        "warm": "Hello there, {name} — lovely to see you.",
    },
    "es": {
        "casual": "¡Hola, {name}!",
        "formal": "Buenos días, {name}.",
        "warm": "¡Hola, querido/a {name}!",
    },
    "ja": {
        "casual": "やあ、{name}!",
        "formal": "{name}さん、こんにちは。",
        "warm": "{name}さん、お会いできて嬉しいです。",
    },
    "fr": {
        "casual": "Salut, {name} !",
        "formal": "Bonjour, {name}.",
        "warm": "Bienvenue, cher/chère {name}.",
    },
}


def lookup(language: str, mood: str) -> str:
    """Return the template string for (language, mood), falling back to English casual."""
    table = GREETINGS.get(language) or GREETINGS["en"]
    return table.get(mood) or table.get("casual") or "Hi {name}!"
