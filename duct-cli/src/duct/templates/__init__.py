"""Template loading for duct workspace files.

Templates are bundled markdown / data files shipped inside the duct package.
Names may include forward slashes to descend into subdirectories — for
example ``load_template("claude_agents/wiki-reader.md")`` reads
``templates/claude_agents/wiki-reader.md``.
"""

from __future__ import annotations

import importlib.resources


def load_template(name: str) -> str:
    """Load a template file by *name* and return its content."""
    ref = importlib.resources.files(__package__).joinpath(name)
    return ref.read_text(encoding="utf-8")
