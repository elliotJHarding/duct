"""Prompt loading for duct agents."""

from __future__ import annotations

import importlib.resources
from string import Template


def load_prompt(name: str, **variables: str) -> str:
    """Load a prompt template by *name* and substitute ``$variable`` placeholders."""
    ref = importlib.resources.files(__package__).joinpath(f"{name}.md")
    text = ref.read_text(encoding="utf-8")
    return Template(text).safe_substitute(variables)
