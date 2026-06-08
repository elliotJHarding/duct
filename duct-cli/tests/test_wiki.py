"""Tests for duct.wiki — entry parsing and maintainer launch."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from unittest.mock import patch

import pytest

from duct.wiki import (
    INDEX_FILENAME,
    list_entries,
    read_entry,
    spawn_maintainer,
    wiki_dir,
)


def _write_entry(root: Path, filename: str, body: str) -> Path:
    directory = wiki_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(dedent(body).lstrip())
    return path


class TestListEntries:
    def test_returns_empty_for_missing_directory(self, tmp_path: Path) -> None:
        assert list_entries(tmp_path) == []

    def test_skips_index_file(self, tmp_path: Path) -> None:
        _write_entry(tmp_path, INDEX_FILENAME, "# Wiki Index\n")
        assert list_entries(tmp_path) == []

    def test_parses_well_formed_entry(self, tmp_path: Path) -> None:
        _write_entry(
            tmp_path,
            "use-spaces.md",
            """
            ---
            name: use-spaces
            type: convention
            description: Indent with spaces, not tabs
            tags: style, formatting
            ---
            # Use spaces

            ## Rule
            Always indent with four spaces.
            """,
        )

        [entry] = list_entries(tmp_path)
        assert entry.name == "use-spaces"
        assert entry.type == "convention"
        assert entry.description == "Indent with spaces, not tabs"
        assert entry.tags == ("style", "formatting")

    def test_skips_entry_without_frontmatter(self, tmp_path: Path) -> None:
        _write_entry(tmp_path, "loose.md", "Just some prose, no frontmatter.\n")
        assert list_entries(tmp_path) == []

    def test_skips_entry_with_invalid_type(self, tmp_path: Path) -> None:
        _write_entry(
            tmp_path,
            "rogue.md",
            """
            ---
            name: rogue
            type: opinion
            description: Not a valid type
            ---
            body
            """,
        )
        assert list_entries(tmp_path) == []

    def test_sorts_by_type_then_name(self, tmp_path: Path) -> None:
        _write_entry(
            tmp_path,
            "two.md",
            """
            ---
            name: two
            type: lesson
            description: Lesson two
            ---
            body
            """,
        )
        _write_entry(
            tmp_path,
            "one.md",
            """
            ---
            name: one
            type: lesson
            description: Lesson one
            ---
            body
            """,
        )
        _write_entry(
            tmp_path,
            "alpha.md",
            """
            ---
            name: alpha
            type: convention
            description: A convention
            ---
            body
            """,
        )

        names = [e.name for e in list_entries(tmp_path)]
        # convention < lesson alphabetically; within lesson, one < two.
        assert names == ["alpha", "one", "two"]

    def test_handles_entry_without_tags(self, tmp_path: Path) -> None:
        _write_entry(
            tmp_path,
            "no-tags.md",
            """
            ---
            name: no-tags
            type: env
            description: Has no tags
            ---
            body
            """,
        )
        [entry] = list_entries(tmp_path)
        assert entry.tags == ()


class TestReadEntry:
    def test_returns_none_when_not_found(self, tmp_path: Path) -> None:
        assert read_entry(tmp_path, "missing") is None

    def test_returns_entry_by_name(self, tmp_path: Path) -> None:
        _write_entry(
            tmp_path,
            "found.md",
            """
            ---
            name: found
            type: lesson
            description: A lesson
            ---
            body
            """,
        )
        entry = read_entry(tmp_path, "found")
        assert entry is not None
        assert entry.name == "found"


class TestSpawnMaintainer:
    def test_runs_claude_in_workspace_root(self, tmp_path: Path) -> None:
        with (
            patch("duct.wiki.shutil.which", return_value="/usr/local/bin/claude"),
            patch("duct.wiki.subprocess.run") as mock_run,
        ):
            mock_run.return_value.returncode = 0
            code = spawn_maintainer(tmp_path)

        assert code == 0
        mock_run.assert_called_once()
        cmd = mock_run.call_args.args[0]
        kwargs = mock_run.call_args.kwargs
        assert cmd[0] == "/usr/local/bin/claude"
        assert "-p" in cmd
        assert kwargs["cwd"] == str(tmp_path)
        assert kwargs["check"] is False
        # The prompt must reference the wiki-maintainer subagent so the
        # session knows what to invoke.
        prompt_idx = cmd.index("-p") + 1
        assert "wiki-maintainer" in cmd[prompt_idx]

    def test_raises_when_claude_missing(self, tmp_path: Path) -> None:
        with patch("duct.wiki.shutil.which", return_value=None):
            with pytest.raises(FileNotFoundError):
                spawn_maintainer(tmp_path)
