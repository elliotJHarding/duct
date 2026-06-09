"""The `duct completion <shell>` command must stay reachable through LazyGroup.

The shell rc hook runs `eval "$(duct completion zsh)"` on every shell startup, so
if the command is dropped from the command registry, every new shell errors with
"No such command 'completion'". This guards that registration.
"""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from duct.cli.main import cli


@pytest.mark.parametrize("shell", ["zsh", "bash", "fish"])
def test_completion_command_emits_script(shell: str) -> None:
    result = CliRunner().invoke(cli, ["completion", shell])
    assert result.exit_code == 0, result.output
    assert result.output.strip()  # a non-empty activation script


def test_completion_is_registered_and_discoverable() -> None:
    # Reachable via the LazyGroup and listed in --help (it is user-facing).
    assert cli.get_command(None, "completion") is not None
    assert "completion" in CliRunner().invoke(cli, ["--help"]).output
