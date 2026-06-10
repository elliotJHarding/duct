"""Credential storage in the OS keychain (via the ``keyring`` library).

The keychain is the single source of truth and is process-independent: the
shell CLI and the launchd background daemon read identical secrets regardless
of which environment variables happen to be set in each process. This is the
key property the older file+env design lacked — ``JIRA_EMAIL``/``JIRA_TOKEN``
in a shell rc are never inherited by launchd, so the daemon silently skipped
Jira while the shell worked.

Secrets are stored under the service ``duct`` with one entry per field
(``jira_email``, ``jira_token``, ``gh_token``).

Resolution:
- Jira: keychain only. No env fallback — that fallback was the source of the
  "works in my shell, skips in the daemon" asymmetry.
- GitHub: keychain, then ``GH_TOKEN``/``GITHUB_TOKEN`` env (a portable CI
  convention), then ``gh auth token`` (the gh CLI's own keychain-backed store,
  which is itself process-independent).

:func:`migrate_legacy_credentials` carries pre-existing secrets (an old
``~/.config/duct/credentials.yaml`` or the ``JIRA_*`` env vars) into the
keychain once, so existing setups keep working after upgrading.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

from duct.global_state import state_dir

_SERVICE = "duct"
_JIRA_EMAIL = "jira_email"
_JIRA_TOKEN = "jira_token"
_GH_TOKEN = "gh_token"

# Pre-keychain location, kept only so :func:`migrate_legacy_credentials` can
# read it once and move its contents into the keychain.
_LEGACY_FILENAME = "credentials.yaml"


def legacy_credentials_file() -> Path:
    return state_dir() / _LEGACY_FILENAME


@dataclass(frozen=True)
class Credentials:
    jira_email: str = ""
    jira_token: str = ""
    gh_token: str = ""


def _get(field: str) -> str:
    """Read one secret from the keychain, tolerating a missing backend."""
    try:
        return keyring.get_password(_SERVICE, field) or ""
    except KeyringError:
        return ""


def _set(field: str, value: str) -> None:
    """Store *value*, or delete the entry when *value* is empty."""
    try:
        if value:
            keyring.set_password(_SERVICE, field, value)
        else:
            keyring.delete_password(_SERVICE, field)
    except PasswordDeleteError:
        pass  # Nothing stored for this field — deleting a no-op is fine.
    except KeyringError:
        pass  # No usable backend; degrade to "no credential stored".


def load_credentials() -> Credentials:
    return Credentials(
        jira_email=_get(_JIRA_EMAIL),
        jira_token=_get(_JIRA_TOKEN),
        gh_token=_get(_GH_TOKEN),
    )


def save_credentials(creds: Credentials) -> None:
    """Persist *creds* to the keychain. Empty fields are cleared."""
    _set(_JIRA_EMAIL, creds.jira_email)
    _set(_JIRA_TOKEN, creds.jira_token)
    _set(_GH_TOKEN, creds.gh_token)


def update_credentials(**kwargs: str) -> Credentials:
    """Merge non-empty *kwargs* into the stored credentials and persist them."""
    current = load_credentials()
    merged = Credentials(
        jira_email=kwargs.get("jira_email") or current.jira_email,
        jira_token=kwargs.get("jira_token") or current.jira_token,
        gh_token=kwargs.get("gh_token") or current.gh_token,
    )
    save_credentials(merged)
    return merged


# ---------------------------------------------------------------------------
# Resolution helpers.
# ---------------------------------------------------------------------------


def resolve_jira_email() -> str:
    return _get(_JIRA_EMAIL)


def resolve_jira_token() -> str:
    return _get(_JIRA_TOKEN)


def resolve_gh_token() -> str:
    """Token from the keychain, env vars, or ``gh auth token``.

    Returns an empty string when none of those produce a value. Callers that
    require a token (e.g. ``duct.config.gh_token``) raise ``AuthError``.
    """
    return resolve_gh_token_with_source()[0]


def resolve_gh_token_with_source() -> tuple[str, str]:
    """Like :func:`resolve_gh_token`, plus a human label for where the token
    was found: "keychain", "environment", or "gh CLI". ``("", "")`` when
    nothing resolves.
    """
    stored = _get(_GH_TOKEN)
    if stored:
        return stored, "keychain"

    env = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if env:
        return env, "environment"

    import shutil
    import subprocess

    if shutil.which("gh"):
        try:
            result = subprocess.run(
                ["gh", "auth", "token"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip(), "gh CLI"
        except Exception:
            pass

    return "", ""


# ---------------------------------------------------------------------------
# One-time migration from the pre-keychain (file + env) design.
# ---------------------------------------------------------------------------


def migrate_legacy_credentials() -> bool:
    """Move pre-keychain secrets into the keychain. Returns True if it wrote.

    Best-effort and idempotent: once the keychain holds Jira credentials this
    is a no-op. Sources, in priority order, are an old
    ``~/.config/duct/credentials.yaml`` and the ``JIRA_*`` / ``GH_*`` env vars.

    Must run from a context that can see those sources — i.e. the user's shell.
    The launchd daemon has neither the env vars nor (after this runs) a reason
    to migrate, so it simply reads the keychain the shell populated.
    """
    if _get(_JIRA_TOKEN):
        return False  # Already migrated.

    legacy = _load_legacy_file()

    jira_email = legacy.jira_email or os.environ.get("JIRA_EMAIL", "")
    jira_token = legacy.jira_token or os.environ.get("JIRA_TOKEN", "")
    gh = legacy.gh_token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""

    if not (jira_email or jira_token or gh):
        return False

    save_credentials(Credentials(
        jira_email=jira_email,
        jira_token=jira_token,
        gh_token=gh or _get(_GH_TOKEN),
    ))
    return True


def _load_legacy_file() -> Credentials:
    """Read the pre-keychain ``credentials.yaml`` if it still exists."""
    path = legacy_credentials_file()
    if not path.exists():
        return Credentials()
    try:
        import yaml

        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return Credentials()
    return Credentials(
        jira_email=str(raw.get("jira_email") or ""),
        jira_token=str(raw.get("jira_token") or ""),
        gh_token=str(raw.get("gh_token") or ""),
    )
