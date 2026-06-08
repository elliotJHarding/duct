"""Workflow agents — reusable session prompts stored under ``agents/``.

An agent is a markdown file with YAML frontmatter (``name`` required,
``description`` optional) whose body is passed verbatim as the session
prompt. Agents are discovered per-workspace and are used both by the
orchestrator (as suggested actions) and by direct user invocation.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from duct.markdown import parse_frontmatter

AGENTS_DIRNAME = "agents"


@dataclass(frozen=True)
class Agent:
    name: str
    description: str
    body: str
    path: Path


def _warn(message: str) -> None:
    sys.stderr.write(f"warning: {message}\n")


def _parse_agent_file(path: Path) -> Agent | None:
    """Parse a single agent file. Returns None and warns on malformed input."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        _warn(f"could not read agent file {path}: {exc}")
        return None

    meta, body = parse_frontmatter(content)
    if not meta:
        _warn(f"agent file {path} has no frontmatter; skipping")
        return None

    name = meta.get("name", "").strip()
    if not name:
        _warn(f"agent file {path} is missing 'name' in frontmatter; skipping")
        return None

    return Agent(
        name=name,
        description=meta.get("description", "").strip(),
        body=body.lstrip("\n"),
        path=path,
    )


def list_agents(root: Path) -> list[Agent]:
    """Discover all agents under ``{root}/agents/*.md``.

    Malformed files are skipped with a warning; well-formed files are
    returned sorted by name.
    """
    agents_dir = root / AGENTS_DIRNAME
    if not agents_dir.is_dir():
        return []

    agents: list[Agent] = []
    for path in sorted(agents_dir.glob("*.md")):
        agent = _parse_agent_file(path)
        if agent is not None:
            agents.append(agent)
    return sorted(agents, key=lambda a: a.name)


def load_agent(root: Path, name: str) -> Agent | None:
    """Return the agent with the given name, or None if not found."""
    for agent in list_agents(root):
        if agent.name == name:
            return agent
    return None
