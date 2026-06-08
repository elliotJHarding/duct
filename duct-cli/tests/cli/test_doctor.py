"""Tests for the duct doctor command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from duct.cli.main import cli
from duct.config import ConfigError


def _init_workspace(root: Path) -> None:
    (root / "config.yaml").write_text(
        f"workspace:\n  root: .\njira:\n  domain: test.atlassian.net\n  jql: assignee = currentUser()\nrepoPaths:\n  - {root}\n"
    )


def test_doctor_no_workspace(tmp_path: Path):
    runner = CliRunner()

    with patch("duct.cli.doctor_cmd.resolve_root", side_effect=ConfigError("No workspace found")):
        result = runner.invoke(cli, ["--workspace-root", str(tmp_path), "doctor"])

    assert result.exit_code != 0
    assert "Cannot continue" in result.output


def test_doctor_invalid_config(tmp_path: Path):
    (tmp_path / "config.yaml").write_text("not: valid: yaml: [[[")
    runner = CliRunner()

    result = runner.invoke(cli, ["--workspace-root", str(tmp_path), "doctor"])

    # Should report a parse error (either exit 1 or report FAIL)
    assert result.exit_code != 0 or "FAIL" in result.output


def test_doctor_missing_workflow(tmp_path: Path):
    _init_workspace(tmp_path)
    runner = CliRunner()

    result = runner.invoke(cli, ["--workspace-root", str(tmp_path), "doctor"])

    assert "FAIL" in result.output
    assert "WORKFLOW.md" in result.output


def test_doctor_missing_auth(tmp_path: Path):
    _init_workspace(tmp_path)
    (tmp_path / "WORKFLOW.md").write_text("# Workflow\n")
    runner = CliRunner()

    # The keychain is empty (conftest) and gh is absent, so no creds resolve.
    with patch("shutil.which", side_effect=lambda cmd: None if cmd in ("gh", "claude") else f"/usr/bin/{cmd}"):
        result = runner.invoke(cli, ["--workspace-root", str(tmp_path), "doctor"])

    assert "FAIL" in result.output


def test_doctor_happy_path(tmp_path: Path):
    _init_workspace(tmp_path)
    (tmp_path / "WORKFLOW.md").write_text("# Workflow\n")
    runner = CliRunner()

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"displayName": "Test User", "login": "testuser"}

    from duct.credentials import Credentials, save_credentials
    save_credentials(Credentials(jira_email="test@test.com", jira_token="token", gh_token="ghtoken"))

    env = {"SHELL": "/bin/bash"}

    # Create a fake .bashrc with completion marker so shell integration passes
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    (fake_home / ".bashrc").write_text('eval "$(_DUCT_COMPLETE=bash_source duct)"\n')

    with (
        patch("shutil.which", return_value="/usr/local/bin/tool"),
        patch("httpx.get", return_value=mock_response),
        patch("pathlib.Path.home", return_value=fake_home),
    ):
        result = runner.invoke(cli, ["--workspace-root", str(tmp_path), "doctor"], env=env)

    assert "All checks passed" in result.output
