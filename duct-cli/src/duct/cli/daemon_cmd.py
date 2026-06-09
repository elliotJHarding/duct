"""duct daemon — the headless background service.

`duct daemon run` is the poll loop launchd executes: it maintains state
freshness (sole owner of periodic sync), reacts to session/ticket changes by
firing macOS notifications, and owns the auto-orchestrate schedule (running the
orchestrator headlessly). State is published to disk under ``.duct/`` — no IPC.

The other subcommands manage the launchd LaunchAgent that keeps it running.
"""

from __future__ import annotations

import datetime as _dt
import os
import plistlib
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import click

from duct import api, daemon_state, paths, run_lock, terminal
from duct.cli.output import error, output, success, warn
from duct.cli.resolve import resolve_root
from duct.config import ConfigError, load_config
from duct.global_state import read_focused_session_pid, state_dir
from duct.notifications import (
    NotificationTracker,
    fire_event,
    orchestrator_event,
)
from duct.notifier import MacNotifier

LABEL = "com.duct.daemon"
ORCHESTRATE_TICK_SECONDS = 60


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _domain_target() -> str:
    return f"gui/{os.getuid()}"


def _pidfile() -> Path:
    return paths.daemon_pidfile()


@click.group()
def daemon() -> None:
    """Manage the duct background daemon (notifications, sync, scheduling)."""


# ---------------------------------------------------------------------------
# duct daemon run — the loop
# ---------------------------------------------------------------------------


@daemon.command("run")
@click.pass_context
def run(ctx: click.Context) -> None:
    """Run the daemon loop in the foreground (this is what launchd executes)."""
    if sys.platform != "darwin":
        error("duct daemon is macOS-only.")
        ctx.exit(1)
        return

    try:
        root = resolve_root(ctx)
        load_config(root)
    except ConfigError as exc:
        # Not set up yet. Don't tight-loop: sleep so launchd's relaunch is paced.
        error(f"duct is not set up ({exc}); retrying shortly.")
        time.sleep(30)
        ctx.exit(1)
        return

    if not _acquire_pidfile():
        error("Another duct daemon is already running.")
        ctx.exit(1)
        return

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    started_iso = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    output(f"duct daemon started for {root}")
    try:
        _loop(root, stop, started_iso)
    finally:
        _release_pidfile()


def _loop(root: Path, stop: threading.Event, started_iso: str) -> None:
    """Run the three cadences until *stop* is set."""
    adapter = _build_adapter()
    cfg = load_config(root)
    notifier = MacNotifier(enabled=cfg.notifications.enabled)
    duct_bin = shutil.which("duct") or sys.argv[0]
    tracker = NotificationTracker(jira_domain=cfg.jira_domain, duct_bin=duct_bin)

    session_interval = max(1, cfg.notifications.session_poll_seconds)
    overview_interval = max(5, cfg.notifications.overview_poll_seconds)

    last_overview = 0.0
    last_orchestrate = 0.0
    daemon_state.write_heartbeat(root, pid=os.getpid(), started_at=started_iso)

    while not stop.is_set():
        now = time.monotonic()

        _safe_tick("sessions", lambda: _session_tick(root, adapter, notifier, tracker, cfg))

        if now - last_overview >= overview_interval:
            cfg = _reload_cfg(root, cfg)
            _safe_tick("overview", lambda: _overview_tick(root, adapter, notifier, tracker, cfg))
            daemon_state.write_heartbeat(root, last_sync_at=_now_iso())
            last_overview = now

        if now - last_orchestrate >= ORCHESTRATE_TICK_SECONDS:
            _safe_tick("orchestrate", lambda: _orchestrate_tick(root, adapter, notifier, cfg))
            last_orchestrate = now

        daemon_state.write_heartbeat(root, pid=os.getpid(), started_at=started_iso)
        stop.wait(session_interval)


def _safe_tick(name: str, fn) -> None:
    """Run a tick body, logging (never raising) so one failure can't kill the loop."""
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 — daemon must survive any tick error
        print(f"[daemon] {name} tick failed: {type(exc).__name__}: {exc}", file=sys.stderr)


def _fire(notifier: MacNotifier, cfg, root: Path, events) -> None:
    sender = cfg.notifications.sender_bundle_id or None
    for event in events:
        if event.kind not in cfg.notifications.event_kinds:
            continue
        fire_event(notifier, root, event, sender=sender)


def _session_tick(root, adapter, notifier, tracker, cfg) -> None:
    sessions = api.get_sessions(root, adapter)
    suppress = _suppressed_session_pids(sessions, adapter, cfg)
    events = tracker.diff_sessions(sessions, suppress_pids=suppress)
    _fire(notifier, cfg, root, events)


def _suppressed_session_pids(sessions, adapter, cfg) -> set[int]:
    """Pids whose notifications to skip: the TUI's docked session, plus the
    session whose terminal is in front of the user (when so configured)."""
    suppress: set[int] = set()
    docked = read_focused_session_pid()
    if docked is not None:
        suppress.add(docked)
    if cfg.notifications.suppress_focused_terminal:
        focused = terminal.focused_session_pid(
            adapter, [s.pid for s in sessions if s.pid]
        )
        if focused is not None:
            suppress.add(focused)
    return suppress


def _overview_tick(root, adapter, notifier, tracker, cfg) -> None:
    results = api.trigger_sync(root, force=False)  # daemon is the sole periodic-sync owner
    for r in results:
        if r.errors:
            print(f"[daemon] sync {r.source}: {'; '.join(r.errors)}", file=sys.stderr)
    overviews = api.get_ticket_overviews(root, "focus", adapter)
    events = tracker.diff_actions(overviews)
    _fire(notifier, cfg, root, events)


def _orchestrate_tick(root, adapter, notifier, cfg) -> None:
    ao = cfg.auto_orchestrate
    if not ao.enabled:
        return
    now = _dt.datetime.now()
    if now.weekday() not in ao.weekdays:
        return
    if not ao.start_hour <= now.hour <= ao.end_hour:
        return
    slot = f"{now.date().isoformat()}:{now.hour:02d}"
    if daemon_state.read_last_orchestrate_slot(root) == slot:
        return
    if not run_lock.acquire(root):
        return  # a manual TUI run (or prior daemon run) is in flight
    try:
        daemon_state.write_last_orchestrate_slot(root, slot)
        before = _pending_action_count(root, adapter)
        if ao.sync_first:
            api.trigger_sync(root, force=False)
        _run_orchestrator(root)
        after = _pending_action_count(root, adapter)
        event = orchestrator_event(max(0, after - before))
        _fire(notifier, cfg, root, [event])
        daemon_state.write_heartbeat(root, last_orchestrate_at=_now_iso())
    finally:
        run_lock.release(root)


def _run_orchestrator(root: Path) -> None:
    from duct import orchestrator

    recorder = orchestrator.RunRecorder(root)
    proc = orchestrator.launch(root)
    assert proc.stdout is not None
    for raw_line in proc.stdout:
        recorder.record(raw_line)
    proc.wait()
    recorder.finalize(proc.returncode)


def _pending_action_count(root: Path, adapter) -> int:
    try:
        overviews = api.get_ticket_overviews(root, "focus", adapter)
    except Exception:
        return 0
    return sum(len(o.pending_actions) for o in overviews)


def _build_adapter():
    try:
        from duct.terminal import get_terminal_adapter

        return get_terminal_adapter("wezterm")
    except Exception:
        return None


def _reload_cfg(root: Path, current):
    try:
        return load_config(root)
    except ConfigError:
        return current


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


# --- single-instance pidfile for direct shell runs ---

def _acquire_pidfile() -> bool:
    path = _pidfile()
    existing = _read_pidfile(path)
    if existing is not None and _pid_alive(existing):
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        return False
    return True


def _release_pidfile() -> None:
    path = _pidfile()
    if _read_pidfile(path) == os.getpid():
        path.unlink(missing_ok=True)


def _read_pidfile(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, OSError):
        return False
    return True


# ---------------------------------------------------------------------------
# launchd lifecycle
# ---------------------------------------------------------------------------


def _launchd_path() -> str:
    """PATH for the launchd agent — includes Homebrew + ~/.local/bin (claude)."""
    return ":".join([
        str(Path.home() / ".local" / "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ])


def _build_plist(root: Path | None = None) -> dict:
    duct_bin = shutil.which("duct") or sys.argv[0]
    logs = paths.home_logs_dir()
    # Bake the resolved workspace root into the args (and DUCT_ROOT) so the
    # daemon resolves it even though launchd gives it no useful cwd and the
    # state.yaml pointer might be absent. This is what makes install robust.
    args = [duct_bin]
    env = {"PATH": _launchd_path()}
    if root is not None:
        args += ["--workspace-root", str(root)]
        env["DUCT_ROOT"] = str(root)
    args += ["daemon", "run"]
    return {
        "Label": LABEL,
        "ProgramArguments": args,
        "EnvironmentVariables": env,
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "StandardOutPath": str(logs / "daemon.log"),
        "StandardErrorPath": str(logs / "daemon.err.log"),
    }


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def install_agent(root: Path | None = None) -> None:
    """Write the launchd plist (pinned to *root*) and load it.

    Also persists *root* as the workspace pointer so every other duct command
    agrees on it. Reused by `duct setup`.
    """
    if root is not None:
        from duct.global_state import set_workspace_path

        set_workspace_path(root)
    state_dir().mkdir(parents=True, exist_ok=True)
    paths.home_logs_dir().mkdir(parents=True, exist_ok=True)
    plist = _plist_path()
    plist.parent.mkdir(parents=True, exist_ok=True)
    with plist.open("wb") as fh:
        plistlib.dump(_build_plist(root), fh)

    res = _launchctl("bootstrap", _domain_target(), str(plist))
    if res.returncode != 0:
        # Already loaded, or older macOS — fall back to legacy load.
        _launchctl("load", "-w", str(plist))


def is_installed() -> bool:
    return _plist_path().exists()


@daemon.command("install")
@click.pass_context
def install(ctx: click.Context) -> None:
    """Install + load the launchd agent so the daemon runs at every login."""
    if sys.platform != "darwin":
        error("duct daemon is macOS-only.")
        ctx.exit(1)
        return
    try:
        root = resolve_root(ctx)
    except ConfigError:
        error(
            "Can't find a duct workspace. Run `duct daemon install` from inside "
            "your workspace, or complete `duct setup` first."
        )
        ctx.exit(1)
        return
    install_agent(root)
    success(
        f"Daemon installed and started ({LABEL}) for {root}. "
        "It will auto-start at login."
    )


@daemon.command("uninstall")
@click.pass_context
def uninstall(ctx: click.Context) -> None:
    """Stop + remove the launchd agent."""
    plist = _plist_path()
    res = _launchctl("bootout", f"{_domain_target()}/{LABEL}")
    if res.returncode != 0:
        _launchctl("unload", "-w", str(plist))
    plist.unlink(missing_ok=True)
    success("Daemon uninstalled.")


@daemon.command("start")
def start() -> None:
    """Start (or restart) the installed daemon."""
    res = _launchctl("kickstart", "-k", f"{_domain_target()}/{LABEL}")
    if res.returncode == 0:
        success("Daemon started.")
    else:
        error(f"Could not start daemon: {res.stderr.strip()} (is it installed?)")


@daemon.command("stop")
def stop_cmd() -> None:
    """Stop the running daemon (launchd will not relaunch until next login/start)."""
    res = _launchctl("kill", "SIGTERM", f"{_domain_target()}/{LABEL}")
    if res.returncode == 0:
        success("Daemon stopped.")
    else:
        error(f"Could not stop daemon: {res.stderr.strip()}")


@daemon.command("status")
@click.pass_context
def status(ctx: click.Context) -> None:
    """Report whether the daemon is installed, running, and its heartbeat age."""
    installed = _plist_path().exists()
    res = _launchctl("print", f"{_domain_target()}/{LABEL}")
    running = res.returncode == 0
    output(f"Installed: {'yes' if installed else 'no'}")
    output(f"Loaded/running: {'yes' if running else 'no'}")
    try:
        root = resolve_root(ctx)
    except ConfigError:
        return
    age = daemon_state.heartbeat_age_seconds(root)
    if age is None:
        warn("No heartbeat found (daemon has not ticked).")
    else:
        output(f"Last heartbeat: {int(age)}s ago")


# ---------------------------------------------------------------------------
# duct daemon notify — fire a notification through the daemon's mechanism
# ---------------------------------------------------------------------------

from duct.cli.notify_cmd import notify  # noqa: E402

daemon.add_command(notify, "notify")
