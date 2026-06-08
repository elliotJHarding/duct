"""Tests for the keychain-backed credential store."""

from __future__ import annotations

import pytest

from duct.credentials import (
    Credentials,
    legacy_credentials_file,
    load_credentials,
    migrate_legacy_credentials,
    resolve_gh_token,
    resolve_jira_email,
    resolve_jira_token,
    save_credentials,
)


def test_save_load_round_trip() -> None:
    save_credentials(Credentials(jira_email="a@b.com", jira_token="tok", gh_token="gh"))
    creds = load_credentials()
    assert (creds.jira_email, creds.jira_token, creds.gh_token) == ("a@b.com", "tok", "gh")


def test_save_clears_empty_fields() -> None:
    save_credentials(Credentials(jira_email="a@b.com", jira_token="tok"))
    save_credentials(Credentials(jira_email="a@b.com"))  # token now empty
    assert load_credentials().jira_token == ""


def test_jira_resolves_keychain_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Jira has no env fallback — the daemon must see exactly what the shell stored."""
    monkeypatch.setenv("JIRA_EMAIL", "shell@example.com")
    monkeypatch.setenv("JIRA_TOKEN", "shell-token")
    assert resolve_jira_email() == ""
    assert resolve_jira_token() == ""

    save_credentials(Credentials(jira_email="keychain@example.com", jira_token="kc"))
    assert resolve_jira_email() == "keychain@example.com"
    assert resolve_jira_token() == "kc"


def test_gh_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """GitHub keeps the portable GH_TOKEN/GITHUB_TOKEN env convention."""
    monkeypatch.setenv("GH_TOKEN", "env-gh")
    assert resolve_gh_token() == "env-gh"
    # Keychain wins over env when present.
    save_credentials(Credentials(gh_token="kc-gh"))
    assert resolve_gh_token() == "kc-gh"


def test_migrate_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_EMAIL", "legacy@example.com")
    monkeypatch.setenv("JIRA_TOKEN", "legacy-token")

    assert migrate_legacy_credentials() is True
    assert resolve_jira_email() == "legacy@example.com"
    assert resolve_jira_token() == "legacy-token"


def test_migrate_from_legacy_file() -> None:
    import yaml

    path = legacy_credentials_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump({"jira_email": "file@example.com", "jira_token": "file-tok"}))

    assert migrate_legacy_credentials() is True
    assert resolve_jira_token() == "file-tok"


def test_migrate_is_idempotent_noop_when_already_set() -> None:
    save_credentials(Credentials(jira_email="a@b.com", jira_token="tok"))
    assert migrate_legacy_credentials() is False


def test_migrate_noop_when_nothing_to_migrate() -> None:
    assert migrate_legacy_credentials() is False
