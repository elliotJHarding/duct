"""Tests for the guided setup flow."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from duct import paths
from duct.cli.main import cli
from duct.credentials import load_credentials
from duct.global_state import load_state


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _fake_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the runner has a real TTY so the setup flow doesn't bail."""
    monkeypatch.setattr("duct.cli.setup_cmd._is_interactive", lambda: True)


def _hermetic_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip developer-machine env that would leak into the test:

    - Real Jira/GitHub env vars would surface as prompt defaults.
    - A recognised ``SHELL`` would trigger the shell-completion auto-fix
      and a confirm prompt against the developer's real rc file.
    - On macOS the daemon-install step would prompt and shell out to
      launchctl; pin the platform so that step is skipped (it has its own
      tests in test_daemon.py).
    """
    for var in ("JIRA_EMAIL", "JIRA_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SHELL", "/bin/unknown-shell")
    monkeypatch.setattr("duct.cli.setup_cmd.sys.platform", "linux")


def test_setup_aborts_on_non_tty(runner: CliRunner) -> None:
    """Setup refuses to run when stdin/stdout aren't a TTY."""
    result = runner.invoke(cli, ["setup"])
    assert result.exit_code == 1
    assert "interactive terminal" in (result.stderr or result.output)


def test_setup_full_happy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: CliRunner,
) -> None:
    """A clean run writes state, scaffolds the workspace, and persists creds."""
    workspace = tmp_path / "workspace"
    _fake_tty(monkeypatch)
    _hermetic_env(monkeypatch)

    # Stub out the live API probes so the test doesn't hit the network.
    monkeypatch.setattr(
        "duct.cli.setup_cmd._jira_user",
        lambda *_args, **_kwargs: (True, "Test User"),
    )
    monkeypatch.setattr(
        "duct.cli.setup_cmd._jql_count",
        lambda *_args, **_kwargs: 42,
    )
    monkeypatch.setattr(
        "duct.cli.setup_cmd._github_user",
        lambda _token: (True, "octocat", ["acme", "globex"]),
    )
    # Skip the `gh auth login` shell-out and shell-rc edit.
    monkeypatch.setattr("shutil.which", lambda name: None)

    # Drive every prompt. Values, in order, must match the flow in
    # ``run_setup``: workspace path → Jira domain → Jira email → Jira token →
    # keep default JQL? (y) → no gh CLI present, prompt for GH PAT → orgs
    # input → keep ~/workspace? → keep ~/projects? → add another? (n) →
    # shell completion bail (unknown shell) → first sync? (n).
    inputs = "\n".join([
        str(workspace),     # workspace path
        "acme.atlassian.net",  # jira domain
        "dev@example.com",     # jira email
        "tok-jira",            # jira token
        "y",                   # keep default JQL
        "ghp_abc",             # GH PAT (no gh CLI)
        "1,2",                 # both orgs
        "n",                   # drop ~/workspace
        "n",                   # drop ~/projects
        "n",                   # add another? no
        "n",                   # first sync? no
        "",
    ])

    result = runner.invoke(cli, ["setup"], input=inputs)
    assert result.exit_code == 0, (result.output, result.stderr)

    # State file points at the workspace.
    state = load_state()
    assert state.workspace_path == workspace.resolve()
    assert paths.config_file(workspace).exists()
    assert paths.workflow_md(workspace).exists()

    # Credentials persisted to the keychain.
    creds = load_credentials()
    assert creds.jira_email == "dev@example.com"
    assert creds.jira_token == "tok-jira"
    assert creds.gh_token == "ghp_abc"

    # config.yaml carries the chosen orgs and Jira domain.
    import yaml
    data = yaml.safe_load(paths.config_file(workspace).read_text())
    assert data["githubOrgs"] == ["acme", "globex"]
    assert data["jira"]["domain"] == "acme.atlassian.net"


def test_setup_skips_jira_step_when_already_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: CliRunner,
) -> None:
    """When the Jira section already passes its live check, the flow
    skips re-prompting for the domain/email/token."""
    workspace = tmp_path / "workspace"
    _fake_tty(monkeypatch)
    _hermetic_env(monkeypatch)

    # Pre-seed a workspace and credentials so the Jira step's
    # "already configured" branch fires.
    from duct.cli.init_cmd import bootstrap_workspace
    from duct.config import WorkspaceConfig, save_config
    from duct.credentials import Credentials, save_credentials
    from duct.global_state import set_workspace_path

    bootstrap_workspace(workspace)
    cfg = WorkspaceConfig(root=workspace, jira_domain="acme.atlassian.net")
    save_config(cfg, workspace)
    save_credentials(Credentials(jira_email="x@y.com", jira_token="tok"))
    set_workspace_path(workspace)

    captured: list[tuple] = []

    def _spy_jira_user(domain, email, token):
        captured.append((domain, email, token))
        return True, "Test User"

    monkeypatch.setattr("duct.cli.setup_cmd._jira_user", _spy_jira_user)
    monkeypatch.setattr("duct.cli.setup_cmd._jql_count", lambda *_a, **_kw: 7)
    monkeypatch.setattr("duct.cli.setup_cmd._github_user", lambda _t: (True, "octocat", []))
    monkeypatch.setattr("shutil.which", lambda name: None)

    inputs = "\n".join([
        str(workspace),       # workspace path (default would also do)
        "y",                  # keep default JQL
        "",                   # no GH PAT
        "n", "n", "n",        # drop both default repo paths, add nothing
        "n",                  # first sync skipped
        "",
    ])
    result = runner.invoke(cli, ["setup"], input=inputs)
    assert result.exit_code == 0, (result.output, result.stderr)

    # The Jira step probed once with the existing values — no re-prompt.
    assert captured == [("acme.atlassian.net", "x@y.com", "tok")]


def test_bare_duct_runs_setup_when_state_missing(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner,
) -> None:
    """Without a configured workspace, bare ``duct`` enters the setup flow."""
    # _isolated_state_dir fixture means there's no state.yaml yet.
    called = {"v": False}

    def _fake_run(ctx):
        called["v"] = True

    monkeypatch.setattr("duct.cli.setup_cmd.run_setup", _fake_run)
    result = runner.invoke(cli, [])
    assert result.exit_code == 0, (result.output, result.stderr)
    assert called["v"] is True


def test_bare_duct_shows_status_when_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: CliRunner,
) -> None:
    """When state is complete, bare ``duct`` prints the status block."""
    workspace = tmp_path / "workspace"
    from duct.cli.init_cmd import bootstrap_workspace
    from duct.credentials import Credentials, save_credentials
    from duct.global_state import set_workspace_path

    bootstrap_workspace(workspace)
    save_credentials(Credentials(jira_email="x@y.com", jira_token="tok"))
    set_workspace_path(workspace)

    # If setup ran anyway, the test would crash on no-TTY input.
    with patch("duct.cli.setup_cmd.run_setup") as mock_run:
        result = runner.invoke(cli, [])

    assert result.exit_code == 0
    assert mock_run.call_count == 0
    assert "Suggested commands" in result.output
    # Rich wraps long paths, so check the leaf rather than the full string.
    assert workspace.name in result.output


def test_require_setup_blocks_subcommand_without_workspace(
    runner: CliRunner,
) -> None:
    """A subcommand that calls ``require_setup`` fails fast when no
    workspace exists yet."""
    # The status command is one of the heaviest workspace consumers — once we
    # wire ``require_setup`` into it, this case is covered there. Until then,
    # exercise ``require_setup`` directly via a tiny harness so the contract
    # is locked down before wiring.
    import click as _click

    from duct.cli.resolve import require_setup

    @_click.command()
    @_click.pass_context
    def _probe(ctx: _click.Context) -> None:
        require_setup(ctx)

    result = runner.invoke(_probe, [], obj={})
    assert result.exit_code == 1
    assert "duct is not set up" in (result.stderr or result.output)
