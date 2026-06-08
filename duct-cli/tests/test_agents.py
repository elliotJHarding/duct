"""Tests for the workflow agents loader."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from duct.agents import AGENTS_DIRNAME, Agent, list_agents, load_agent


def _write_agent(root: Path, filename: str, body: str) -> Path:
    agents_dir = root / AGENTS_DIRNAME
    agents_dir.mkdir(exist_ok=True)
    path = agents_dir / filename
    path.write_text(dedent(body).lstrip())
    return path


class TestListAgents:
    def test_returns_empty_when_no_agents_dir(self, tmp_path: Path) -> None:
        assert list_agents(tmp_path) == []

    def test_parses_well_formed_agent(self, tmp_path: Path) -> None:
        _write_agent(tmp_path, "draft-ac.md", """
            ---
            name: draft-ac
            description: Draft acceptance criteria
            ---

            Read BACKGROUND.md and write AC.md.
        """)

        agents = list_agents(tmp_path)

        assert len(agents) == 1
        agent = agents[0]
        assert agent.name == "draft-ac"
        assert agent.description == "Draft acceptance criteria"
        assert agent.body.startswith("Read BACKGROUND.md")
        assert "---" not in agent.body

    def test_sorts_agents_by_name(self, tmp_path: Path) -> None:
        _write_agent(tmp_path, "z-last.md", "---\nname: z-last\n---\nbody")
        _write_agent(tmp_path, "a-first.md", "---\nname: a-first\n---\nbody")

        names = [a.name for a in list_agents(tmp_path)]

        assert names == ["a-first", "z-last"]

    def test_skips_file_without_frontmatter(
        self, tmp_path: Path, capsys,
    ) -> None:
        _write_agent(tmp_path, "no-frontmatter.md", "Just a body, no frontmatter.")
        _write_agent(tmp_path, "ok.md", "---\nname: ok\n---\nbody")

        agents = list_agents(tmp_path)

        assert [a.name for a in agents] == ["ok"]
        assert "no frontmatter" in capsys.readouterr().err

    def test_skips_file_missing_name(self, tmp_path: Path, capsys) -> None:
        _write_agent(
            tmp_path, "nameless.md",
            "---\ndescription: something\n---\nbody",
        )
        _write_agent(tmp_path, "ok.md", "---\nname: ok\n---\nbody")

        agents = list_agents(tmp_path)

        assert [a.name for a in agents] == ["ok"]
        assert "missing 'name'" in capsys.readouterr().err

    def test_description_defaults_to_empty(self, tmp_path: Path) -> None:
        _write_agent(tmp_path, "minimal.md", "---\nname: minimal\n---\nbody")

        agents = list_agents(tmp_path)

        assert agents[0].description == ""


class TestLoadAgent:
    def test_returns_agent_by_name(self, tmp_path: Path) -> None:
        _write_agent(tmp_path, "draft-ac.md", """
            ---
            name: draft-ac
            description: Draft AC
            ---

            body
        """)

        agent = load_agent(tmp_path, "draft-ac")

        assert isinstance(agent, Agent)
        assert agent.name == "draft-ac"

    def test_returns_none_for_unknown_name(self, tmp_path: Path) -> None:
        _write_agent(tmp_path, "draft-ac.md", "---\nname: draft-ac\n---\nbody")

        assert load_agent(tmp_path, "nope") is None

    def test_returns_none_when_no_agents_dir(self, tmp_path: Path) -> None:
        assert load_agent(tmp_path, "anything") is None
