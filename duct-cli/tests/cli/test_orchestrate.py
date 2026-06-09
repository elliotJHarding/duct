"""Tests for duct orchestrate command."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from duct import paths
from duct.cli.main import cli
from duct.orchestrator import (
    RunRecorder,
    build_prompt,
    format_stream_event,
)
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

    def test_dry_run_always_includes_stream_json(self, tmp_path: Path) -> None:
        """stream-json is always requested so runs can be recorded to .duct/runs/."""
        _init_workspace(tmp_path)
        runner = CliRunner()
        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            result = runner.invoke(
                cli,
                ["--workspace-root", str(tmp_path), "orchestrate", "--dry-run"],
            )
        assert result.exit_code == 0
        assert "--verbose" in result.output
        assert "--output-format" in result.output
        assert "stream-json" in result.output

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
        prompt = build_prompt(None)
        assert "duct orchestrator" in prompt
        assert "WORKFLOW.md" in prompt

    def test_prompt_includes_sync_boundary(self) -> None:
        prompt = build_prompt(None)
        assert "duct sync" in prompt
        assert "do not create it manually" in prompt
        assert ".archive/" in prompt

    def test_prompt_owns_orchestrator_md(self) -> None:
        prompt = build_prompt(None)
        # The orchestrator now maintains per-ticket ORCHESTRATOR.md notes
        # (the priority-list workspace artefact has been removed).
        assert "ORCHESTRATOR.md" in prompt
        assert "ground truth" in prompt

    def test_prompt_includes_ticket_focus(self) -> None:
        prompt = build_prompt("PROJ-42")
        assert "Focus this session on ticket PROJ-42" in prompt

    def test_prompt_no_ticket_focus_when_none(self) -> None:
        prompt = build_prompt(None)
        assert "Focus this session" not in prompt

    def test_prompt_resolves_fork_model_placeholder(self) -> None:
        prompt = build_prompt(None)
        # Default fork model is substituted into the fan-out instructions; no
        # unresolved $fork_model placeholder may remain.
        assert "$fork_model" not in prompt
        assert "model: 'sonnet'" in prompt

    def test_prompt_uses_custom_fork_model(self) -> None:
        prompt = build_prompt(None, fork_model="haiku")
        assert "model: 'haiku'" in prompt
        assert "model: 'sonnet'" not in prompt

    def test_prompt_includes_self_evaluation(self) -> None:
        """Self-evaluation behaviour and the improve_workflow action shape live
        in the orchestrator prompt (system design), not in WORKFLOW.md (user config).

        The framing should be broad — friction reduction, captured context,
        workflow tuning — not narrowly about drift detection.
        """
        prompt = build_prompt(None).lower()
        assert "improve_workflow" in prompt
        assert ".duct/actions.yaml" in prompt
        # Broad framing markers
        assert "friction" in prompt
        assert "context" in prompt


class TestStreamFormatting:
    def test_init_event(self) -> None:
        event = json.dumps({"type": "system", "subtype": "init", "model": "opus-4"})
        result = format_stream_event(event)
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
        result = format_stream_event(event)
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
        result = format_stream_event(event)
        assert result is not None
        assert "Analyzing the ticket" in result

    def test_result_event(self) -> None:
        event = json.dumps({
            "type": "result",
            "duration_seconds": 12.3,
            "cost_usd": 0.04,
            "num_turns": 4,
        })
        result = format_stream_event(event)
        assert result is not None
        assert "4 turns" in result
        assert "12.3s" in result
        assert "$0.04" in result

    def test_skips_unknown_event(self) -> None:
        event = json.dumps({"type": "content_block_delta"})
        assert format_stream_event(event) is None

    def test_invalid_json(self) -> None:
        assert format_stream_event("not json{") is None

    def test_long_text_truncated(self) -> None:
        long_text = "x" * 300
        event = json.dumps({
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": long_text}],
            },
        })
        result = format_stream_event(event)
        assert result is not None
        assert "..." in result
        assert len(result) < 300


class TestRunRecorder:
    def _stream(self) -> list[str]:
        """A representative orchestrator stream."""
        return [
            json.dumps({"type": "system", "subtype": "init", "model": "opus-4"}),
            json.dumps({
                "type": "assistant",
                "message": {"content": [
                    {"type": "tool_use", "name": "Read", "input": {"file_path": "WORKFLOW.md"}},
                ]},
            }),
            json.dumps({
                "type": "assistant",
                "message": {"content": [
                    {"type": "text", "text": "Found two tickets needing attention."},
                    {"type": "tool_use", "name": "Edit", "input": {"file_path": "PS-1/orchestrator/ORCHESTRATOR.md"}},
                ]},
            }),
            json.dumps({
                "type": "assistant",
                "message": {"content": [
                    {"type": "text", "text": "PS-1 needs review attention this run."},
                ]},
            }),
            json.dumps({
                "type": "result",
                "duration_seconds": 12.3,
                "cost_usd": 0.04,
                "num_turns": 4,
            }),
        ]

    def test_writes_file_under_runs_dir(self, tmp_path: Path) -> None:
        recorder = RunRecorder(tmp_path, ticket_key="PS-1")
        for line in self._stream():
            recorder.record(line)
        path = recorder.finalize(returncode=0)

        assert path.parent == paths.runs_dir(tmp_path)
        assert path.exists()
        assert path.suffix == ".md"

    def test_summary_contains_frontmatter_and_timeline(self, tmp_path: Path) -> None:
        recorder = RunRecorder(tmp_path, ticket_key="PS-1")
        for line in self._stream():
            recorder.record(line)
        content = recorder.finalize(returncode=0).read_text()

        # Frontmatter
        assert content.startswith("---\n")
        assert "ticket: PS-1" in content
        assert "model: opus-4" in content
        assert "turns: 4" in content
        assert "duration_seconds: 12.3" in content
        assert "cost_usd: 0.04" in content
        assert "exit_code: 0" in content

        # Conclusion uses the last assistant text block
        assert "## Conclusion" in content
        assert "> PS-1 needs review attention this run." in content

        # Timeline includes tool uses and text
        assert "## Timeline" in content
        assert "**Read** `WORKFLOW.md`" in content
        assert "**Edit** `PS-1/orchestrator/ORCHESTRATOR.md`" in content
        assert "Found two tickets needing attention." in content

    def test_summary_without_ticket_omits_ticket_frontmatter(self, tmp_path: Path) -> None:
        recorder = RunRecorder(tmp_path)
        for line in self._stream():
            recorder.record(line)
        content = recorder.finalize(returncode=0).read_text()

        assert "ticket:" not in content

    def test_last_assistant_text(self, tmp_path: Path) -> None:
        recorder = RunRecorder(tmp_path)
        for line in self._stream():
            recorder.record(line)

        assert recorder.last_assistant_text() == "PS-1 needs review attention this run."

    def test_ignores_invalid_json(self, tmp_path: Path) -> None:
        recorder = RunRecorder(tmp_path)
        recorder.record("not json{")
        recorder.record(json.dumps({"type": "result", "num_turns": 1, "duration_seconds": 0.1, "cost_usd": 0.0}))
        content = recorder.finalize(returncode=0).read_text()

        assert "turns: 1" in content

    def test_empty_stream_still_writes_file(self, tmp_path: Path) -> None:
        recorder = RunRecorder(tmp_path)
        path = recorder.finalize(returncode=1)

        assert path.exists()
        content = path.read_text()
        assert "exit_code: 1" in content
        assert "_(no final output)_" in content
        assert "_(no recorded activity)_" in content


class TestOrchestratorPromptReferencesRuns:
    def test_prompt_mentions_runs_dir(self) -> None:
        prompt = build_prompt(None)
        assert ".duct/runs/" in prompt
