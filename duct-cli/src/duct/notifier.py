"""macOS notifications via terminal-notifier.

Lives in duct-cli (not duct-tui) so the background daemon — the sole owner of
desktop notifications — can fire them. The TUI no longer notifies directly.
"""

from __future__ import annotations

import importlib.resources
import shutil
import subprocess
import sys
from pathlib import Path

# terminal-notifier (and wezterm) install here under Homebrew, which is NOT on
# the minimal PATH a launchd agent inherits. `shutil.which` therefore returns
# None under launchd and the notifier would silently no-op. Fall back to these
# absolute locations so the daemon works regardless of the inherited PATH.
_BIN_FALLBACK_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")


def _resolve_bin(name: str) -> str | None:
    """Resolve a binary, falling back to known Homebrew dirs when not on PATH."""
    found = shutil.which(name)
    if found:
        return found
    for directory in _BIN_FALLBACK_DIRS:
        candidate = Path(directory) / name
        if candidate.exists():
            return str(candidate)
    return None


# Notification kinds that originate from the orchestrator. They get the
# conductor icon so the user can tell them apart from session notifications
# (done/waiting), which keep the brand clawd icon.
_ORCHESTRATOR_KINDS = frozenset({"pending-action", "orchestrator", "orchestrator-action"})


def _bundled_icon(filename: str) -> str | None:
    """Resolve a bundled icon path inside the duct package."""
    try:
        ref = importlib.resources.files("duct") / filename
        with importlib.resources.as_file(ref) as p:
            if p.exists():
                return str(p)
    except Exception:
        pass
    return None


def _default_icon() -> str | None:
    """Resolve the bundled clawd.png brand icon path."""
    return _bundled_icon("clawd.png")


def _conductor_icon() -> str | None:
    """Resolve the bundled conductor.png icon for orchestrator notifications."""
    return _bundled_icon("conductor.png")


class MacNotifier:
    """Fire-and-forget notifications via ``terminal-notifier``.

    A no-op when disabled, on non-darwin platforms, or when
    terminal-notifier is not installed.
    """

    def __init__(self, *, enabled: bool, icon: str | None = None) -> None:
        self._enabled = enabled and sys.platform == "darwin"
        self._bin: str | None = _resolve_bin("terminal-notifier") if self._enabled else None
        if self._bin is None:
            self._enabled = False
        self._icon: str | None = icon or (_default_icon() if self._enabled else None)
        self._orchestrator_icon: str | None = _conductor_icon() if self._enabled else None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def notify(
        self,
        title: str,
        body: str,
        *,
        subtitle: str | None = None,
        group: str | None = None,
        sound: str | None = None,
        open_url: str | None = None,
        execute: str | None = None,
        content_image: str | None = None,
        sender: str | None = None,
        kind: str | None = None,
    ) -> bool:
        if not self._enabled:
            return False
        cmd = self._build_cmd(
            title, body, subtitle, group, sound, open_url, execute, content_image, sender, kind,
        )
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            return result.returncode == 0
        except Exception:
            return False

    def _build_cmd(
        self,
        title: str,
        body: str,
        subtitle: str | None,
        group: str | None,
        sound: str | None,
        open_url: str | None,
        execute: str | None,
        content_image: str | None,
        sender: str | None,
        kind: str | None,
    ) -> list[str]:
        assert self._bin is not None
        cmd = [self._bin, "-title", title, "-message", body]
        if subtitle:
            cmd += ["-subtitle", subtitle]
        if group:
            cmd += ["-group", group]
        if sound:
            cmd += ["-sound", sound]
        # -execute (run a command on click) takes precedence over -open; a
        # session notification focuses its terminal rather than opening a URL.
        if execute:
            cmd += ["-execute", execute]
        elif open_url:
            cmd += ["-open", open_url]
        if sender:
            cmd += ["-sender", sender]
        # A per-notification image wins; otherwise attach the conductor icon for
        # orchestrator notifications, falling back to the brand clawd icon.
        default_icon = (
            self._orchestrator_icon
            if kind in _ORCHESTRATOR_KINDS and self._orchestrator_icon
            else self._icon
        )
        image = content_image or default_icon
        if image:
            cmd += ["-contentImage", image]
        return cmd
