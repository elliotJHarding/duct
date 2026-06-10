"""Tests for the duct init command (hidden scaffold command)."""

from pathlib import Path

import yaml
from click.testing import CliRunner

from duct import paths
from duct.cli.main import cli


def test_init_creates_all_files(tmp_path: Path) -> None:
    """init should create the toolkit config + the generated .claude/CLAUDE.md shim."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--workspace-root", str(tmp_path), "init"])

    assert result.exit_code == 0, result.output
    assert paths.config_file(tmp_path).exists()
    assert paths.workflow_md(tmp_path).exists()

    claude_md = paths.root_claude_md(tmp_path)
    assert claude_md.exists()
    shim = claude_md.read_text(encoding="utf-8")
    assert "@../toolkit/CLAUDE.md" in shim
    # The wiki is opt-in (off by default) — no wiki wiring in a fresh shim.
    assert "@../toolkit/wiki/INDEX.md" not in shim
    assert not paths.wiki_dir(tmp_path).exists()


def test_init_wires_wiki_when_enabled(tmp_path: Path) -> None:
    """With wiki.enabled, init seeds the wiki and the shim imports its index."""
    from duct.cli.setup_core import set_wiki

    runner = CliRunner()
    runner.invoke(cli, ["--workspace-root", str(tmp_path), "init"])
    set_wiki(tmp_path, True)

    shim = paths.root_claude_md(tmp_path).read_text(encoding="utf-8")
    assert "@../toolkit/wiki/INDEX.md" in shim
    assert "wiki-reader" in shim  # generated guidance travels with the toggle
    assert paths.wiki_index(tmp_path).exists()


def test_init_is_idempotent(tmp_path: Path) -> None:
    """Running init twice should not overwrite existing files."""
    runner = CliRunner()

    # First run — creates files
    runner.invoke(cli, ["--workspace-root", str(tmp_path), "init"])

    # Write custom content to WORKFLOW.md
    workflow_path = paths.workflow_md(tmp_path)
    custom_content = "# My Custom Workflow\n"
    workflow_path.write_text(custom_content)

    # Second run — should not overwrite
    result = runner.invoke(cli, ["--workspace-root", str(tmp_path), "init"])
    assert result.exit_code == 0, result.output
    assert workflow_path.read_text() == custom_content


def test_init_respects_workspace_root(tmp_path: Path) -> None:
    """init should create files in the directory specified by --workspace-root."""
    target = tmp_path / "custom" / "workspace"
    runner = CliRunner()
    result = runner.invoke(cli, ["--workspace-root", str(target), "init"])

    assert result.exit_code == 0, result.output
    assert paths.config_file(target).exists()
    assert paths.workflow_md(target).exists()


def test_init_config_yaml_is_valid(tmp_path: Path) -> None:
    """The generated config.yaml should be valid YAML."""
    runner = CliRunner()
    runner.invoke(cli, ["--workspace-root", str(tmp_path), "init"])

    config_path = paths.config_file(tmp_path)
    data = yaml.safe_load(config_path.read_text())
    assert isinstance(data, dict)
    assert "workspace" in data
    assert "jira" in data


def test_init_json_output(tmp_path: Path) -> None:
    """init with --json should produce JSON output containing created files."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--json", "--workspace-root", str(tmp_path), "init"])

    assert result.exit_code == 0, result.output
    import json

    lines = [line for line in result.output.strip().splitlines() if line.strip()]
    assert len(lines) >= 1
    data = json.loads(lines[0])
    assert "created" in data
