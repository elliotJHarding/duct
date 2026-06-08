"""Sandbox configuration for Claude Code sessions."""

from __future__ import annotations

import json
from pathlib import Path

from duct.config import SandboxConfig

_SETTINGS_TEMPLATE_FILENAME = "settings.template.json"


def build_settings(config: SandboxConfig) -> dict:
    """Produce a Claude Code settings dict from sandbox configuration."""
    filesystem: dict[str, list[str]] = {}
    if config.allow_write:
        filesystem["allowWrite"] = list(config.allow_write)
    if config.deny_read:
        filesystem["denyRead"] = list(config.deny_read)

    sandbox: dict = {
        "enabled": config.enabled,
        "autoAllowBashIfSandboxed": config.auto_allow_bash,
        "filesystem": filesystem,
    }

    if config.allowed_domains:
        sandbox["network"] = {"allowedDomains": list(config.allowed_domains)}

    return {"sandbox": sandbox}


def write_settings(target_dir: Path, config: SandboxConfig) -> Path:
    """Write sandbox config into ``{target_dir}/.claude/settings.json``.

    If the file already exists, only the ``sandbox`` key is replaced;
    other keys (e.g. ``env``) are preserved.  Returns the path written.
    """
    claude_dir = target_dir / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    settings_path = claude_dir / "settings.json"

    existing: dict = {}
    if settings_path.exists():
        try:
            existing = json.loads(settings_path.read_text())
        except (json.JSONDecodeError, OSError):
            existing = {}

    new_settings = build_settings(config)
    existing["sandbox"] = new_settings["sandbox"]

    settings_path.write_text(json.dumps(existing, indent=2) + "\n")
    return settings_path


def load_settings_template(root: Path) -> dict | None:
    """Load ``settings.template.json`` from *root*, or return None if missing/invalid.

    The template is an optional top-level JSON object sitting next to
    ``config.yaml``.  Its top-level keys are merged into each ticket's
    ``.claude/settings.json`` during sync so that, e.g., telemetry env vars
    only apply to work sessions.  Returns None when the file is absent,
    unreadable, malformed, or not a JSON object.
    """
    template_path = root / _SETTINGS_TEMPLATE_FILENAME
    if not template_path.exists():
        return None
    try:
        data = json.loads(template_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def merge_settings_template(target_dir: Path, template: dict) -> Path:
    """Merge *template*'s top-level keys into ``{target_dir}/.claude/settings.json``.

    Existing keys not present in *template* (e.g. ``sandbox``) are preserved.
    A ``sandbox`` key in *template* is ignored so it cannot clobber sandbox
    config written by :func:`write_settings`.  Creates the file and the
    ``.claude/`` directory if they do not yet exist.
    """
    claude_dir = target_dir / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    settings_path = claude_dir / "settings.json"

    existing: dict = {}
    if settings_path.exists():
        try:
            existing = json.loads(settings_path.read_text())
        except (json.JSONDecodeError, OSError):
            existing = {}

    for key, value in template.items():
        if key == "sandbox":
            continue
        existing[key] = value

    settings_path.write_text(json.dumps(existing, indent=2) + "\n")
    return settings_path
