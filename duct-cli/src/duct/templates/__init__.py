"""Template loading for duct workspace files."""

from __future__ import annotations

import importlib.resources


def load_template(name: str) -> str:
    """Load a template file by *name* and return its content."""
    ref = importlib.resources.files(__package__).joinpath(name)
    return ref.read_text(encoding="utf-8")
