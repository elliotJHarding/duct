"""Shared test fixtures for duct."""

from pathlib import Path

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError


class _MemoryKeyring(KeyringBackend):
    """In-memory keyring backend so tests never touch the real OS keychain."""

    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        try:
            del self._store[(service, username)]
        except KeyError as exc:
            raise PasswordDeleteError(str(exc)) from exc


@pytest.fixture(autouse=True)
def _isolated_credentials(tmp_path_factory, monkeypatch) -> Path:
    """Isolate credential storage for every test.

    Two leaks to seal off:
    - ``~/.config/duct`` (legacy credential file + state) → a tmp dir.
    - The OS keychain → a fresh in-memory backend per test.

    Also strip the developer's real ``JIRA_*`` / ``GH_*`` env vars so neither
    the resolvers nor the one-time migration pick them up; tests that need
    credentials seed them explicitly via ``save_credentials``.
    """
    state = tmp_path_factory.mktemp("duct_state")
    monkeypatch.setenv("DUCT_STATE_DIR", str(state))

    previous = keyring.get_keyring()
    keyring.set_keyring(_MemoryKeyring())

    for var in ("JIRA_EMAIL", "JIRA_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(var, raising=False)

    yield state

    keyring.set_keyring(previous)


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace directory."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws
