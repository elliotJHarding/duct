"""Tests for duct orchestrate command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from duct.cli.main import cli
from duct.cli.orchestrate_cmd import _build_prompt, _format_stream_event
from duct.config import WorkspaceConfig, save_config


def _init_workspace(root: Path) -> None:
    cfg = WorkspaceConfig(root=root)
    save_config(cfg, root)


class TestOrchestrate:
    def test_dry_run_shows_command(self, tmp_path: Path) -> None:
        _init_workspace(tmp_path)
        runner = CliRunner()
        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            result = runner.invoke(
                cli,
                ["--workspace-root", str(tmp_path), "orchestrate", "--dry-run"],
            )
        assert result.exit_code == 0
        assert "/usr/local/bin/claude" in result.output
        assert "--add-dir" in result.output

    def test_dry_run_with_ticket(self, tmp_path: Path) -> None:
        _init_workspace(tmp_path)
        runner = CliRunner()
        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            result = runner.invoke(
                cli,
                [
                    "--workspace-root", str(tmp_path),
                    "orchestrate", "--ticket", "ERSC-1278", "--dry-run",
                ],
            )
        assert result.exit_code == 0
        assert "ERSC-1278" in result.output

    def test_missing_claude_binary(self, tmp_path: Path) -> None:
        _init_workspace(tmp_path)
        runner = CliRunner()
        with patch("shutil.which", return_value=None):
            result = runner.invoke(
                cli,
                ["--workspace-root", str(tmp_path), "orchestrate"],
            )
        assert result.exit_code != 0
        assert "not found" in result.output

    def test_dry_run_includes_all_tools(self, tmp_path: Path) -> None:
        """All standard tools are always included."""
        _init_workspace(tmp_path)
        runner = CliRunner()
        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            result = runner.invoke(
                cli,
                ["--workspace-root", str(tmp_path), "orchestrate", "--dry-run"],
            )
        for tool in ("Read", "Glob", "Grep", "Write", "Edit", "Bash"):
            assert tool in result.output

    def test_dry_run_verbose_adds_stream_json(self, tmp_path: Path) -> None:
        _init_workspace(tmp_path)
        runner = CliRunner()
        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            result = runner.invoke(
                cli,
                ["--workspace-root", str(tmp_path), "orchestrate", "--dry-run", "--verbose"],
            )
        assert result.exit_code == 0
        assert "--verbose" in result.output
        assert "--output-format" in result.output
        assert "stream-json" in result.output

    def test_dry_run_without_verbose_no_stream_json(self, tmp_path: Path) -> None:
        _init_workspace(tmp_path)
        runner = CliRunner()
        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            result = runner.invoke(
                cli,
                ["--workspace-root", str(tmp_path), "orchestrate", "--dry-run"],
            )
        assert result.exit_code == 0
        assert "stream-json" not in result.output

    def test_json_dry_run(self, tmp_path: Path) -> None:
        _init_workspace(tmp_path)
        runner = CliRunner()
        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            result = runner.invoke(
                cli,
                [
                    "--json",
                    "--workspace-root", str(tmp_path),
                    "orchestrate", "--dry-run",
                ],
            )
        assert result.exit_code == 0
        assert '"command"' in result.output


class TestPromptContent:
    def test_prompt_loads_from_file(self) -> None:
        prompt = _build_prompt(None)
        assert "duct orchestrator" in prompt
        assert "PRIORITY.md" in prompt
        assert "WORKFLOW.md" in prompt

    def test_prompt_includes_sync_boundary(self) -> None:
        prompt = _build_prompt(None)
        assert "duct sync" in prompt
        assert "do not create it manually" in prompt
        assert ".archive/" in prompt

    def test_prompt_includes_priority_guidance(self) -> None:
        prompt = _build_prompt(None)
        assert "maintain PRIORITY.md" in prompt
        assert "markdown list item" in prompt
        assert "ticket key" in prompt

    def test_prompt_includes_ticket_focus(self) -> None:
        prompt = _build_prompt("PROJ-42")
        assert "Focus this session on ticket PROJ-42" in prompt

    def test_prompt_no_ticket_focus_when_none(self) -> None:
        prompt = _build_prompt(None)
        assert "Focus this session" not in prompt

    def test_prompt_has_no_trust_references(self) -> None:
        prompt = _build_prompt(None)
        assert "trust" not in prompt.lower()
        assert "propose" not in prompt.lower()
        assert "PROPOSED_ACTIONS" not in prompt


class TestStreamFormatting:
    def test_init_event(self) -> None:
        event = json.dumps({"type": "system", "subtype": "init", "model": "opus-4"})
        result = _format_stream_event(event)
        assert result is not None
        assert "model=opus-4" in result

    def test_tool_use_event(self) -> None:
        event = json.dumps({
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "Read", "input": {"file_path": "src/foo.py"}},
                ],
            },
        })
        result = _format_stream_event(event)
        assert result is not None
        assert "Read" in result
        assert "src/foo.py" in result

    def test_text_event(self) -> None:
        event = json.dumps({
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Analyzing the ticket..."},
                ],
            },
        })
        result = _format_stream_event(event)
        assert result is not None
        assert "Analyzing the ticket" in result

    def test_result_event(self) -> None:
        event = json.dumps({
            "type": "result",
            "duration_seconds": 12.3,
            "cost_usd": 0.04,
            "num_turns": 4,
        })
        result = _format_stream_event(event)
        assert result is not None
        assert "4 turns" in result
        assert "12.3s" in result
        assert "$0.04" in result

    def test_skips_unknown_event(self) -> None:
        event = json.dumps({"type": "content_block_delta"})
        assert _format_stream_event(event) is None

    def test_invalid_json(self) -> None:
        assert _format_stream_event("not json{") is None

    def test_long_text_truncated(self) -> None:
        long_text = "x" * 300
        event = json.dumps({
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": long_text}],
            },
        })
        result = _format_stream_event(event)
        assert result is not None
        assert "..." in result
        assert len(result) < 300
