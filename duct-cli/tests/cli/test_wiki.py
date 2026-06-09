"""Tests for the duct wiki CLI command group."""

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


def _write_entry(root: Path, filename: str, body: str) -> None:
    directory = paths.wiki_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(dedent(body).lstrip())


class TestWikiList:
    def test_reports_empty_wiki(self, tmp_path: Path) -> None:
        _init_workspace(tmp_path)
        result = CliRunner().invoke(
            cli, ["--workspace-root", str(tmp_path), "wiki", "list"],
        )
        assert result.exit_code == 0, result.output
        assert "No wiki entries" in result.output

    def test_lists_entries(self, tmp_path: Path) -> None:
        _init_workspace(tmp_path)
        _write_entry(
            tmp_path,
            "rebuild-tui.md",
            """
            ---
            name: rebuild-tui
            type: env
            description: Rebuild duct-tui via pipx after every change
            ---
            body
            """,
        )

        result = CliRunner().invoke(
            cli, ["--workspace-root", str(tmp_path), "wiki", "list"],
        )

        assert result.exit_code == 0, result.output
        assert "rebuild-tui" in result.output
        assert "env" in result.output
        assert "pipx" in result.output


class TestWikiShow:
    def test_errors_when_entry_missing(self, tmp_path: Path) -> None:
        _init_workspace(tmp_path)
        result = CliRunner().invoke(
            cli, ["--workspace-root", str(tmp_path), "wiki", "show", "ghost"],
        )
        assert result.exit_code == 1
        assert "ghost" in result.output

    def test_prints_entry_body(self, tmp_path: Path) -> None:
        _init_workspace(tmp_path)
        _write_entry(
            tmp_path,
            "use-spaces.md",
            """
            ---
            name: use-spaces
            type: convention
            description: Indent with spaces
            ---
            # Use spaces

            ## Rule
            Always indent with four spaces.
            """,
        )

        result = CliRunner().invoke(
            cli, ["--workspace-root", str(tmp_path), "wiki", "show", "use-spaces"],
        )

        assert result.exit_code == 0, result.output
        assert "Use spaces" in result.output
        assert "four spaces" in result.output


class TestWikiReview:
    def test_launches_maintainer_in_workspace_root(self, tmp_path: Path) -> None:
        _init_workspace(tmp_path)

        with (
            patch("duct.wiki.shutil.which", return_value="/usr/local/bin/claude"),
            patch("duct.wiki.subprocess.run") as mock_run,
        ):
            mock_run.return_value.returncode = 0
            result = CliRunner().invoke(
                cli, ["--workspace-root", str(tmp_path), "wiki", "review"],
            )

        assert result.exit_code == 0, result.output
        mock_run.assert_called_once()
        cmd = mock_run.call_args.args[0]
        prompt_idx = cmd.index("-p") + 1
        assert "wiki-maintainer" in cmd[prompt_idx]
        assert mock_run.call_args.kwargs["cwd"] == str(tmp_path)

    def test_surfaces_missing_claude_error(self, tmp_path: Path) -> None:
        _init_workspace(tmp_path)
        with patch("duct.wiki.shutil.which", return_value=None):
            result = CliRunner().invoke(
                cli, ["--workspace-root", str(tmp_path), "wiki", "review"],
            )
        assert result.exit_code == 1
        assert "claude" in result.output.lower()


class TestInitSeedsWiki:
    """Cross-cutting test: `duct init` should seed the wiki and subagents."""

    def test_init_creates_wiki_index_and_subagents(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(cli, ["--workspace-root", str(tmp_path), "init"])
        assert result.exit_code == 0, result.output

        index = paths.wiki_index(tmp_path)
        assert index.exists()
        index_text = index.read_text(encoding="utf-8")
        assert "Wiki Index" in index_text
        assert "Entries" in index_text

        for name in ("wiki-reader", "wiki-contributor", "wiki-maintainer"):
            agent_file = tmp_path / ".claude" / "agents" / f"{name}.md"
            assert agent_file.exists(), f"missing seeded subagent: {name}"
            body = agent_file.read_text(encoding="utf-8")
            assert f"name: {name}" in body
