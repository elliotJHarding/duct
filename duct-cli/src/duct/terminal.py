"""Terminal emulator interaction for duct."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from duct import perf

# Homebrew install location, which is NOT on the minimal PATH a launchd agent
# inherits. The daemon shells out to `wezterm cli` for pane-text status, so
# `shutil.which` alone would return None under launchd. Fall back to absolute
# locations so the daemon enriches status identically to the interactive TUI.
_WEZTERM_FALLBACK_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")


def _wezterm_bin() -> str | None:
    """Resolve the wezterm binary, falling back to known Homebrew dirs."""
    found = shutil.which("wezterm")
    if found:
        return found
    for directory in _WEZTERM_FALLBACK_DIRS:
        candidate = Path(directory) / "wezterm"
        if candidate.exists():
            return str(candidate)
    return None


_WEZTERM_BUNDLE_ID = "com.github.wez.wezterm"


def _raise_wezterm() -> None:
    """Bring the WezTerm GUI to the foreground (best-effort, macOS only).

    `wezterm cli activate-pane` switches panes inside the mux but doesn't raise
    the app, so a notification-click jump is invisible without this.
    """
    if platform.system() != "Darwin":
        return
    try:
        subprocess.run(
            ["open", "-b", _WEZTERM_BUNDLE_ID],
            capture_output=True, timeout=5,
        )
    except Exception:
        pass


def _timed_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
    """`subprocess.run` with a perf span recorded around the call.

    Used in place of bare `subprocess.run` for the wezterm/ps/AppleScript
    hot paths so every call lands in `~/.duct/perf.jsonl` for monitoring.
    The span name is derived from the binary name and (for `wezterm cli`
    invocations) the subcommand — so the perf log distinguishes
    ``wezterm cli list`` from ``wezterm cli get-text`` etc.
    """
    program = Path(cmd[0]).name if cmd else "subprocess"
    span = program
    if program == "wezterm" and len(cmd) >= 3 and cmd[1] == "cli":
        span = f"wezterm.{cmd[2]}"
    with perf.Timer(span, cmd=program):
        return subprocess.run(cmd, **kwargs)


@dataclass(frozen=True)
class PaneContext:
    """Layout context for a terminal pane within its tab."""

    pane_cols: int
    tab_cols: int
    is_split: bool

    @property
    def pane_ratio(self) -> float:
        """Fraction of the tab width occupied by this pane (0.0–1.0)."""
        if self.tab_cols == 0:
            return 1.0
        return self.pane_cols / self.tab_cols


class TerminalAdapter(Protocol):
    """Protocol for terminal emulator interaction."""

    @property
    def name(self) -> str: ...

    def open_tab(self, cwd: Path, command: list[str], title: str | None = None) -> bool:
        """Open a new terminal tab, run command. Returns success."""
        ...

    def focus_tab(self, pid: int) -> bool:
        """Switch focus to the tab running the given PID."""
        ...

    def get_tab_title(self, pid: int) -> str | None:
        """Read the tab title for a session PID."""
        ...

    # --- Pane management (for TUI split-pane session viewing) ---

    def get_own_pane_id(self) -> int | None:
        """Return the pane ID of the current process, or None if unavailable."""
        ...

    def find_pane_for_pid(self, pid: int) -> int | None:
        """Find the terminal pane ID hosting the given process PID."""
        ...

    def focused_pane_id(self) -> int | None:
        """Return the pane ID the terminal GUI currently has focused, or None."""
        ...

    def dock_pane(self, anchor_pane_id: int, target_pane_id: int, percent: int = 70) -> bool:
        """Move target pane into a right split next to anchor pane."""
        ...

    def undock_pane(self, pane_id: int) -> bool:
        """Return a pane to its own independent tab."""
        ...

    def activate_pane(self, pane_id: int) -> bool:
        """Focus a pane by its ID."""
        ...

    def get_pane_text(self, pane_id: int) -> str | None:
        """Capture visible terminal text (with ANSI escapes) from a pane."""
        ...

    def get_pane_context(self, pane_id: int) -> PaneContext | None:
        """Return layout context for a pane: its width, tab width, and split state."""
        ...

    def spawn_pane(self, cwd: Path, command: list[str]) -> int | None:
        """Spawn a process in a new terminal pane. Returns pane ID, or None on failure."""
        ...

    def send_text(self, pane_id: int, text: str, paste: bool = True) -> bool:
        """Send text to a pane. ``paste=True`` uses bracketed paste; False sends raw keystrokes."""
        ...


class ITerm2Adapter:
    """iTerm2 terminal adapter using AppleScript."""

    @property
    def name(self) -> str:
        return "iterm2"

    def open_tab(self, cwd: Path, command: list[str], title: str | None = None) -> bool:
        if platform.system() != "Darwin":
            return False
        cmd_str = " ".join(str(c) for c in command)
        title_line = f'set name of current session of current tab of current window to "{title}"' if title else ""
        script = (
            'tell application "iTerm2"\n'
            "    activate\n"
            "    tell current window\n"
            "        create tab with default profile\n"
            "        tell current session of current tab\n"
            f'            write text "cd {cwd} && {cmd_str}"\n'
            "        end tell\n"
            "    end tell\n"
            f"    {title_line}\n"
            "end tell\n"
        )
        try:
            result = _timed_run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def focus_tab(self, pid: int) -> bool:
        tty = get_tty(pid)
        if not tty:
            return False
        return focus_terminal_tab(tty)

    def get_tab_title(self, pid: int) -> str | None:
        tty = get_tty(pid)
        if not tty:
            return None
        return get_terminal_title(tty)

    def get_own_pane_id(self) -> int | None:
        raise NotImplementedError("Pane management not supported in iTerm2 adapter")

    def find_pane_for_pid(self, pid: int) -> int | None:
        raise NotImplementedError("Pane management not supported in iTerm2 adapter")

    def focused_pane_id(self) -> int | None:
        return None

    def dock_pane(self, anchor_pane_id: int, target_pane_id: int, percent: int = 70) -> bool:
        raise NotImplementedError("Pane management not supported in iTerm2 adapter")

    def undock_pane(self, pane_id: int) -> bool:
        raise NotImplementedError("Pane management not supported in iTerm2 adapter")

    def activate_pane(self, pane_id: int) -> bool:
        raise NotImplementedError("Pane management not supported in iTerm2 adapter")

    def get_pane_text(self, pane_id: int) -> str | None:
        return None

    def get_pane_context(self, pane_id: int) -> PaneContext | None:
        return None

    def spawn_pane(self, cwd: Path, command: list[str]) -> int | None:
        return None

    def send_text(self, pane_id: int, text: str, paste: bool = True) -> bool:
        return False


class WeztermAdapter:
    """WezTerm terminal adapter using wezterm CLI."""

    @property
    def name(self) -> str:
        return "wezterm"

    def open_tab(self, cwd: Path, command: list[str], title: str | None = None) -> bool:
        wezterm_bin = _wezterm_bin()
        if not wezterm_bin:
            return False
        try:
            cmd = [wezterm_bin, "cli", "spawn", "--cwd", str(cwd)]
            if command:
                cmd.append("--")
                cmd.extend(str(c) for c in command)
            result = _timed_run(cmd, capture_output=True, text=True, timeout=5)
            return result.returncode == 0
        except Exception:
            return False

    def focus_tab(self, pid: int) -> bool:
        tty = get_tty(pid)
        if not tty:
            return False
        return focus_terminal_tab(tty)

    def get_tab_title(self, pid: int) -> str | None:
        tty = get_tty(pid)
        if not tty:
            return None
        return get_terminal_title(tty)

    def get_own_pane_id(self) -> int | None:
        raw = os.environ.get("WEZTERM_PANE")
        if raw is None:
            return None
        try:
            return int(raw)
        except ValueError:
            return None

    def find_pane_for_pid(self, pid: int) -> int | None:
        tty = get_tty(pid)
        if not tty:
            return None
        for pane in _wezterm_list_panes():
            if pane.get("tty_name", "").endswith(tty):
                return pane["pane_id"]
        return None

    def focused_pane_id(self) -> int | None:
        """Pane the WezTerm GUI currently has focused, via ``cli list-clients``.

        Each connected client reports its ``focused_pane_id``. When more than
        one client is attached we trust the least-idle one (the client the user
        most recently interacted with). Returns None if no client reports a
        focused pane.
        """
        wezterm_bin = _wezterm_bin()
        if not wezterm_bin:
            return None
        try:
            result = _timed_run(
                [wezterm_bin, "cli", "list-clients", "--format", "json"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return None
            clients = json.loads(result.stdout)
        except Exception:
            return None

        best_pane: int | None = None
        best_idle: float | None = None
        for client in clients:
            pane = client.get("focused_pane_id")
            if pane is None:
                continue
            idle = client.get("idle_time", {}).get("secs", 0)
            if best_idle is None or idle < best_idle:
                best_pane, best_idle = pane, idle
        return best_pane

    def dock_pane(self, anchor_pane_id: int, target_pane_id: int, percent: int = 70) -> bool:
        wezterm_bin = _wezterm_bin()
        if not wezterm_bin:
            return False
        try:
            result = _timed_run(
                [
                    wezterm_bin, "cli", "split-pane",
                    "--right",
                    "--pane-id", str(anchor_pane_id),
                    "--move-pane-id", str(target_pane_id),
                    "--percent", str(percent),
                ],
                capture_output=True, text=True, timeout=5,
            )
            _invalidate_pane_list_cache()
            # split-pane activates the target pane as a side effect,
            # which is the desired behaviour — focus goes to the session.
            return result.returncode == 0
        except Exception:
            return False

    def undock_pane(self, pane_id: int) -> bool:
        wezterm_bin = _wezterm_bin()
        if not wezterm_bin:
            return False
        try:
            result = _timed_run(
                [wezterm_bin, "cli", "move-pane-to-new-tab", "--pane-id", str(pane_id)],
                capture_output=True, text=True, timeout=5,
            )
            _invalidate_pane_list_cache()
            return result.returncode == 0
        except Exception:
            return False

    def activate_pane(self, pane_id: int) -> bool:
        wezterm_bin = _wezterm_bin()
        if not wezterm_bin:
            return False
        try:
            result = _timed_run(
                [wezterm_bin, "cli", "activate-pane", "--pane-id", str(pane_id)],
                capture_output=True, text=True, timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def get_pane_text(self, pane_id: int) -> str | None:
        wezterm_bin = _wezterm_bin()
        if not wezterm_bin:
            return None
        try:
            result = _timed_run(
                [wezterm_bin, "cli", "get-text", "--escapes", "--pane-id", str(pane_id)],
                capture_output=True, text=True, timeout=5,
            )
            return result.stdout if result.returncode == 0 else None
        except Exception:
            return None

    def get_pane_context(self, pane_id: int) -> PaneContext | None:
        panes = _wezterm_list_panes()
        target = None
        for p in panes:
            if p.get("pane_id") == pane_id:
                target = p
                break
        if target is None:
            return None

        pane_cols = target.get("size", {}).get("cols", 0)
        tab_id = target.get("tab_id")
        siblings = [p for p in panes if p.get("tab_id") == tab_id]
        tab_cols = sum(p.get("size", {}).get("cols", 0) for p in siblings)

        return PaneContext(
            pane_cols=pane_cols,
            tab_cols=tab_cols,
            is_split=len(siblings) > 1,
        )

    def spawn_pane(self, cwd: Path, command: list[str]) -> int | None:
        wezterm_bin = _wezterm_bin()
        if not wezterm_bin:
            return None
        try:
            cmd = [wezterm_bin, "cli", "spawn", "--cwd", str(cwd), "--", *command]
            result = _timed_run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0 and result.stdout.strip():
                return int(result.stdout.strip())
        except Exception:
            pass
        return None

    def send_text(self, pane_id: int, text: str, paste: bool = True) -> bool:
        wezterm_bin = _wezterm_bin()
        if not wezterm_bin:
            return False
        try:
            cmd = [wezterm_bin, "cli", "send-text", "--pane-id", str(pane_id)]
            if not paste:
                cmd.append("--no-paste")
            cmd.extend(["--", text])
            result = _timed_run(cmd, capture_output=True, text=True, timeout=10)
            return result.returncode == 0
        except Exception:
            return False


_PANE_LIST_TTL = 5.0
_pane_list_cache: tuple[float, list[dict]] = (0.0, [])


def _wezterm_list_panes() -> list[dict]:
    """Fetch all WezTerm panes as a list of dicts.

    Results are cached for `_PANE_LIST_TTL` seconds so that consecutive
    operations (sidebar status poll, then preview, then dock) don't each
    pay a separate `wezterm cli list` round trip. The cache is explicitly
    invalidated by `dock_pane` and `undock_pane` — the only operations
    that mutate the pane topology in a way callers care about.
    """
    global _pane_list_cache
    cached_at, cached_data = _pane_list_cache
    now = time.monotonic()
    if now - cached_at < _PANE_LIST_TTL and cached_data:
        return cached_data

    wezterm_bin = _wezterm_bin()
    if not wezterm_bin:
        return []
    try:
        result = _timed_run(
            [wezterm_bin, "cli", "list", "--format", "json"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            _pane_list_cache = (now, data)
            return data
    except Exception:
        pass
    return []


def _invalidate_pane_list_cache() -> None:
    """Reset the TTL cache. Used by tests to guarantee a fresh fetch."""
    global _pane_list_cache
    _pane_list_cache = (0.0, [])


def get_terminal_adapter(name: str = "iterm2") -> TerminalAdapter:
    """Get a terminal adapter by name."""
    adapters = {
        "iterm2": ITerm2Adapter,
        "wezterm": WeztermAdapter,
    }
    cls = adapters.get(name)
    if cls is None:
        raise ValueError(f"Unknown terminal adapter: {name}. Available: {', '.join(adapters)}")
    return cls()


# --- Low-level terminal functions (shared by adapters) ---


def frontmost_bundle_id() -> str | None:
    """Return the bundle id of the frontmost macOS app, or None.

    Uses ``lsappinfo`` (a couple of cheap calls, no AppleScript). macOS only;
    returns None elsewhere or when the frontmost app has no bundle id.
    """
    if platform.system() != "Darwin":
        return None
    try:
        front = _timed_run(["lsappinfo", "front"], capture_output=True, text=True, timeout=2)
        asn = front.stdout.strip()
        if not asn:
            return None
        info = _timed_run(
            ["lsappinfo", "info", "-only", "bundleID", asn],
            capture_output=True, text=True, timeout=2,
        )
    except Exception:
        return None
    # Output looks like: "CFBundleIdentifier"="com.github.wez.wezterm"
    raw = info.stdout.strip()
    if "=" not in raw:
        return None
    value = raw.split("=", 1)[1].strip().strip('"')
    return value if value and value != "NULL" else None


def _pane_tty_name(pane_id: int) -> str | None:
    """The ``tty_name`` (e.g. /dev/ttys003) of a WezTerm pane, from the cached list."""
    for pane in _wezterm_list_panes():
        if pane.get("pane_id") == pane_id:
            return pane.get("tty_name")
    return None


def focused_session_pid(adapter: TerminalAdapter | None, pids: list[int]) -> int | None:
    """The pid from *pids* whose terminal is in front of the user, else None.

    "In front" requires both halves: WezTerm is the frontmost macOS app **and**
    the session's pane is WezTerm's focused pane. Either alone is not enough —
    a focused pane in a backgrounded WezTerm means the user is looking
    elsewhere and still wants the notification.

    Returns None for non-WezTerm adapters or whenever focus can't be
    established; the caller then treats no session as focused (notifies).
    """
    if adapter is None or getattr(adapter, "name", "") != "wezterm":
        return None
    if frontmost_bundle_id() != _WEZTERM_BUNDLE_ID:
        return None
    pane_id = adapter.focused_pane_id()
    if pane_id is None:
        return None
    tty_name = _pane_tty_name(pane_id)
    if not tty_name:
        return None
    for pid, tty in get_ttys([p for p in pids if p]).items():
        if tty_name.endswith(tty):
            return pid
    return None


def get_tty(pid: int) -> str | None:
    """Get the TTY device name for a given PID via ps."""
    try:
        result = _timed_run(
            ["ps", "-o", "tty=", "-p", str(pid)],
            capture_output=True, text=True,
        )
        tty = result.stdout.strip()
        return tty if tty and tty != "?" else None
    except Exception:
        return None


def get_ttys(pids: list[int]) -> dict[int, str]:
    """Batch tty lookup. One ``ps`` invocation per call.

    On a loaded macOS system the per-pid ``ps`` round trip is occasionally
    seconds slow (we've observed p95 > 2 s). Calling N ps subprocesses
    sequentially produces a serial bottleneck during session refresh.
    Coalescing into one ``ps -p PID1,PID2,...`` invocation pays the
    process-table walk only once.

    Returns ``{pid: tty}`` for pids whose tty is present and isn't ``?``.
    Pids whose tty cannot be determined are omitted from the result.
    """
    if not pids:
        return {}
    pid_args = ",".join(str(p) for p in pids)
    result_map: dict[int, str] = {}
    try:
        result = _timed_run(
            ["ps", "-o", "pid=,tty=", "-p", pid_args],
            capture_output=True, text=True,
        )
        for line in result.stdout.splitlines():
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            try:
                pid_int = int(parts[0])
            except ValueError:
                continue
            tty = parts[1].strip()
            if tty and tty != "?":
                result_map[pid_int] = tty
    except Exception:
        pass
    return result_map


def get_terminal_title(tty: str) -> str | None:
    """Read the tab/session title for a given TTY from wezterm's pane list.

    The previous iTerm AppleScript fallback was removed: it added a
    3 s-per-call cliff for every alive non-wezterm session during startup.
    Sessions running outside wezterm now keep their transcript-derived
    topic instead of probing iTerm.
    """
    for pane in _wezterm_list_panes():
        if pane.get("tty_name", "").endswith(tty):
            return pane.get("title") or pane.get("tab_title")
    return None


def focus_terminal_tab(tty: str) -> bool:
    """Try to activate the terminal tab that owns tty. Returns True on success."""
    wezterm_bin = _wezterm_bin()
    if wezterm_bin:
        for pane in _wezterm_list_panes():
            if pane.get("tty_name", "").endswith(tty):
                try:
                    _timed_run(
                        [wezterm_bin, "cli", "activate-pane", "--pane-id", str(pane["pane_id"])],
                        capture_output=True, timeout=5,
                    )
                    # activate-pane only switches the pane inside WezTerm's mux;
                    # it does NOT raise the GUI. When focus is triggered from a
                    # notification click (frontmost app is elsewhere), also bring
                    # WezTerm to the foreground so the jump is actually visible.
                    _raise_wezterm()
                    return True
                except Exception:
                    pass

    if platform.system() == "Darwin":
        script = (
            'tell application "iTerm2"\n'
            "    repeat with aWindow in windows\n"
            "        repeat with aTab in tabs of aWindow\n"
            "            repeat with aSession in sessions of aTab\n"
            f'                if tty of aSession ends with "{tty}" then\n'
            "                    select aTab\n"
            "                    tell aWindow to select\n"
            "                    return true\n"
            "                end if\n"
            "            end repeat\n"
            "        end repeat\n"
            "    end repeat\n"
            "end tell\n"
            "return false\n"
        )
        try:
            result = _timed_run(
                ["osascript", "-e", script],
                capture_output=True, text=True,
            )
            if result.returncode == 0 and "true" in result.stdout.strip().lower():
                return True
        except Exception:
            pass

    return False
