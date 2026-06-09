"""Tests for the duct agent CLI command group."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from unittest.mock import patch

from click.testing import CliRunner

from duct import paths
from duct.cli.main import cli


def _init_workspace(root: Path) -> None:
    paths.toolkit_dir(root).mkdir(parents=True, exist_ok=True)
    paths.config_file(root).write_text("workspace:\n  root: .\n")


def _write_agent(root: Path, filename: str, body: str) -> None:
    agents_dir = paths.agents_dir(root)
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / filename).write_text(dedent(body).lstrip())


def _prepare_ticket(root: Path, key: str) -> Path:
    ticket_dir = root / f"{key}-feature"
    (ticket_dir / "orchestrator").mkdir(parents=True)
    return ticket_dir


class TestAgentList:
    def test_lists_agents(self, tmp_path: Path) -> None:
        _init_workspace(tmp_path)
        _write_agent(tmp_path, "draft-ac.md", """
            ---
            name: draft-ac
            description: Draft acceptance criteria
            ---

            body
        """)

        result = CliRunner().invoke(
            cli, ["--workspace-root", str(tmp_path), "agent", "list"],
        )

        assert result.exit_code == 0, result.output
        assert "draft-ac" in result.output
        assert "Draft acceptance criteria" in result.output

    def test_reports_empty(self, tmp_path: Path) -> None:
        _init_workspace(tmp_path)

        result = CliRunner().invoke(
            cli, ["--workspace-root", str(tmp_path), "agent", "list"],
        )

        assert result.exit_code == 0, result.output
        assert "No agents" in result.output


class TestAgentRun:
    def test_launches_session_with_agent_body_as_prompt(self, tmp_path: Path) -> None:
        _init_workspace(tmp_path)
        _prepare_ticket(tmp_path, "PROJ-1")
        _write_agent(tmp_path, "draft-ac.md", """
            ---
            name: draft-ac
            description: Draft AC
            ---

            Read BACKGROUND.md and draft AC.md.
        """)

        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("duct.cli.agent_cmd.subprocess.run") as mock_run,
        ):
            result = CliRunner().invoke(
                cli,
                [
                    "--workspace-root", str(tmp_path),
                    "agent", "run", "draft-ac", "--ticket", "PROJ-1",
                ],
            )

        assert result.exit_code == 0, result.output
        cmd = mock_run.call_args[0][0]
        # Agent body must flow through to the claude prompt (last positional arg).
        prompt_arg = cmd[-1]
        assert "Read BACKGROUND.md and draft AC.md." in prompt_arg
        # prepare_session injects ticket context preamble
        assert "PROJ-1" in prompt_arg
        # Interactive launch — print mode must not be in the cmd.
        assert "-p" not in cmd
        assert "--print" not in cmd

    def test_unknown_agent_exits_non_zero(self, tmp_path: Path) -> None:
        _init_workspace(tmp_path)
        _prepare_ticket(tmp_path, "PROJ-1")

        result = CliRunner().invoke(
            cli,
            [
                "--workspace-root", str(tmp_path),
                "agent", "run", "nope", "--ticket", "PROJ-1",
            ],
        )

        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_missing_ticket_flag_is_required(self, tmp_path: Path) -> None:
        _init_workspace(tmp_path)
        _write_agent(tmp_path, "x.md", "---\nname: x\n---\nbody")

        result = CliRunner().invoke(
            cli,
            ["--workspace-root", str(tmp_path), "agent", "run", "x"],
        )

        assert result.exit_code != 0
        assert "ticket" in result.output.lower()

    def test_unknown_ticket_reports_error(self, tmp_path: Path) -> None:
        _init_workspace(tmp_path)
        _write_agent(tmp_path, "x.md", "---\nname: x\n---\nbody")

        result = CliRunner().invoke(
            cli,
            [
                "--workspace-root", str(tmp_path),
                "agent", "run", "x", "--ticket", "NOPE-99",
            ],
        )

        assert result.exit_code != 0
        assert "NOPE-99" in result.output
