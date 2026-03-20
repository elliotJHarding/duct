"""Tests for the duct config command (CLI-level)."""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from click.testing import CliRunner

from duct.cli.main import cli


def _init_workspace(root: Path) -> None:
    (root / "config.yaml").write_text(
        "workspace:\n  root: .\njira:\n  domain: test.atlassian.net\n  jql: assignee = currentUser()\nrepoPaths: []\n"
    )


# ---------------------------------------------------------------------------
# config show
# ---------------------------------------------------------------------------


def test_config_show(tmp_path: Path):
    _init_workspace(tmp_path)
    runner = CliRunner()

    result = runner.invoke(cli, ["--workspace-root", str(tmp_path), "config"])

    assert result.exit_code == 0, result.output
    assert "test.atlassian.net" in result.output


def test_config_show_json(tmp_path: Path):
    _init_workspace(tmp_path)
    runner = CliRunner()

    result = runner.invoke(cli, ["--json", "--workspace-root", str(tmp_path), "config"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip())
    assert data["jira_domain"] == "test.atlassian.net"


def test_config_show_omits_trust(tmp_path: Path):
    _init_workspace(tmp_path)
    runner = CliRunner()

    result = runner.invoke(cli, ["--workspace-root", str(tmp_path), "config"])

    assert result.exit_code == 0, result.output
    assert "trust" not in result.output.lower()


# ---------------------------------------------------------------------------
# config set
# ---------------------------------------------------------------------------


def test_config_set_jira_domain(tmp_path: Path):
    _init_workspace(tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        cli, ["--workspace-root", str(tmp_path), "config", "set", "jira.domain", "new.atlassian.net"]
    )

    assert result.exit_code == 0, result.output
    raw = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert raw["jira"]["domain"] == "new.atlassian.net"


def test_config_set_bogus_key(tmp_path: Path):
    _init_workspace(tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        cli, ["--workspace-root", str(tmp_path), "config", "set", "bogus.key", "value"]
    )

    assert result.exit_code != 0
    assert "Unknown config key" in result.output


def test_config_set_trust_key_rejected(tmp_path: Path):
    """Trust keys are no longer settable."""
    _init_workspace(tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        cli, ["--workspace-root", str(tmp_path), "config", "set", "trust.gitCommit", "auto"]
    )

    assert result.exit_code != 0
    assert "Unknown config key" in result.output


def test_config_set_interval_non_integer(tmp_path: Path):
    _init_workspace(tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        cli, ["--workspace-root", str(tmp_path), "config", "set", "syncIntervals.jira", "abc"]
    )

    assert result.exit_code != 0
    assert "integer" in result.output.lower()


# ---------------------------------------------------------------------------
# config add-repo-path / remove-repo-path
# ---------------------------------------------------------------------------


def test_config_add_remove_repo_path_roundtrip(tmp_path: Path):
    _init_workspace(tmp_path)
    runner = CliRunner()
    test_path = str(tmp_path / "my-repos")

    # Add
    result = runner.invoke(
        cli, ["--workspace-root", str(tmp_path), "config", "add-repo-path", test_path]
    )
    assert result.exit_code == 0, result.output
    raw = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert test_path in raw["repoPaths"]

    # Remove
    result = runner.invoke(
        cli, ["--workspace-root", str(tmp_path), "config", "remove-repo-path", test_path]
    )
    assert result.exit_code == 0, result.output
    raw = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert test_path not in raw.get("repoPaths", [])


def test_config_add_repo_path_duplicate(tmp_path: Path):
    _init_workspace(tmp_path)
    runner = CliRunner()
    test_path = str(tmp_path / "my-repos")

    runner.invoke(cli, ["--workspace-root", str(tmp_path), "config", "add-repo-path", test_path])
    result = runner.invoke(
        cli, ["--workspace-root", str(tmp_path), "config", "add-repo-path", test_path]
    )

    assert result.exit_code != 0
    assert "already" in result.output.lower()
