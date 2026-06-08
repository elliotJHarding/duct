"""Performance regression tests for the duct-tui hot paths.

These tests fake ``subprocess.run`` so they can count exactly how many
``wezterm cli`` / ``git`` / ``ps`` / ``osascript`` invocations a given
operation triggers, and inject a per-call delay to enforce a wall-clock
budget. They guard against the regressions tracked in
``/Users/hardinge/.claude/plans/there-are-some-severe-noble-haven.md``:

  - ``_load_initial_data`` must not invoke ``git status`` twice for the
    same repo (get_tickets / get_ticket_overviews used to overlap).
  - ``_load_initial_data`` must not call ``osascript`` (the iTerm
    AppleScript fallback in ``get_terminal_title`` was a 3 s-per-session
    cliff).
  - Session preview / dock must not pay two sequential ``wezterm cli``
    round trips per action (the pane-list cache plus pre-warm).

Tests are deterministic — fake ``subprocess.run`` returns canned stdout
keyed off the command name, and ``perf.disabled()`` keeps the production
perf log clean.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from duct import api, perf
from duct.terminal import _invalidate_pane_list_cache


# --- Fake subprocess.run --------------------------------------------------


class FakeRun:
    """Stand-in for ``subprocess.run`` that records every invocation.

    With ``serialize=True`` every call holds a shared lock for the duration
    of its sleep, so concurrent callers compound rather than overlapping.
    This mirrors how the wezterm IPC daemon serialises requests on its
    single socket — the property that the production parallelism bug
    relies on.
    """

    # Default session topology — pid -> tty short name.
    # Tests that need more sessions pass `pid_to_tty` explicitly.
    _DEFAULT_PID_TO_TTY = {1234: "ttys001", 5678: "ttys002"}

    def __init__(
        self,
        delay_ms: float = 0.0,
        serialize: bool = False,
        pid_to_tty: dict[int, str] | None = None,
    ):
        self.calls: list[tuple[tuple[str, ...], str | None]] = []
        self.delay_ms = delay_ms
        self._serialize = serialize
        self._lock = threading.Lock()
        self._concurrent = 0
        self._max_concurrent = 0
        self._stat_lock = threading.Lock()
        self._pid_to_tty = dict(pid_to_tty or self._DEFAULT_PID_TO_TTY)

    def __call__(self, cmd, *args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        cmd_t = tuple(str(c) for c in cmd)
        cwd = kwargs.get("cwd")
        self.calls.append((cmd_t, cwd))
        with self._stat_lock:
            self._concurrent += 1
            if self._concurrent > self._max_concurrent:
                self._max_concurrent = self._concurrent
        try:
            if self._serialize:
                with self._lock:
                    if self.delay_ms:
                        time.sleep(self.delay_ms / 1000.0)
            elif self.delay_ms:
                time.sleep(self.delay_ms / 1000.0)
        finally:
            with self._stat_lock:
                self._concurrent -= 1
        return subprocess.CompletedProcess(
            args=cmd, returncode=0,
            stdout=self._stdout(cmd_t), stderr="",
        )

    @property
    def max_concurrent(self) -> int:
        return self._max_concurrent

    def _stdout(self, cmd_t: tuple[str, ...]) -> str:
        program = Path(cmd_t[0]).name
        if program == "git":
            if "branch" in cmd_t:
                return "main\n"
            return ""  # clean working tree
        if program == "wezterm":
            if "list" in cmd_t:
                panes = [
                    {"pane_id": idx + 1, "tty_name": f"/dev/{tty}",
                     "tab_id": idx + 1,
                     "size": {"cols": 80, "rows": 24},
                     "title": f"session-{idx + 1}"}
                    for idx, tty in enumerate(self._pid_to_tty.values())
                ]
                return json.dumps(panes)
            if "get-text" in cmd_t:
                # A realistic non-empty capture so the pane-text cache gets
                # populated (the cache treats empty strings as "no useful
                # content" and skips them).
                return "session output line 1\nline 2\n"
            if "split-pane" in cmd_t:
                return "42"
            return ""
        if program == "ps":
            # Map session pid -> tty so that find_pane_for_pid can match.
            # ps is invoked in two shapes: per-pid (`-o tty= -p PID`) and
            # batched (`-o pid=,tty= -p PID1,PID2`). The fake handles both.
            try:
                pid_idx = cmd_t.index("-p")
                pid_arg = cmd_t[pid_idx + 1]
            except (ValueError, IndexError):
                return ""
            tty_for = {str(p): t for p, t in self._pid_to_tty.items()}
            pids = pid_arg.split(",")
            if "pid=,tty=" in cmd_t:
                lines = [
                    f"{p:>5} {tty_for[p]}" for p in pids if p in tty_for
                ]
                return "\n".join(lines) + ("\n" if lines else "")
            # Single-pid shape.
            return f"{tty_for.get(pids[0], '')}\n" if pids[0] in tty_for else ""
        if program == "osascript":
            return "iterm-title"
        if program == "pgrep":
            return ""
        return ""

    # --- Convenience accessors used by assertions ---

    def calls_for(self, program: str) -> list[tuple[tuple[str, ...], str | None]]:
        return [(c, w) for c, w in self.calls if Path(c[0]).name == program]

    def git_status_cwds(self) -> list[str | None]:
        return [
            cwd for c, cwd in self.calls
            if Path(c[0]).name == "git" and "status" in c
        ]


# --- Fixtures -------------------------------------------------------------


@pytest.fixture(autouse=True)
def _disable_perf_log():
    """Don't pollute ~/.duct/perf.jsonl from the test suite."""
    from duct import pane_status

    with perf.disabled():
        # Module-level caches must not leak between tests — they would
        # mask cache-cold assertions and let one test's setup satisfy
        # the next test's expectations.
        pane_status._pane_text_cache.clear()
        yield
        pane_status._pane_text_cache.clear()


@pytest.fixture
def fake_run(monkeypatch: pytest.MonkeyPatch) -> FakeRun:
    fake = FakeRun()
    # Patch globally so that every call site (terminal, api, session, etc.)
    # routes through the same recorder. This is safe inside a test because
    # nothing else in the duct code path is supposed to spawn a subprocess.
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(
        "duct.terminal.shutil.which",
        lambda binary: f"/usr/bin/{binary}",
    )
    _invalidate_pane_list_cache()
    return fake


@pytest.fixture
def fake_sessions(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Two alive sessions, one per project dir."""
    template = [
        {"session_id": "s1", "pid": 1234, "cwd": "/tmp/PROJ-1-feature",
         "alive": True, "status": "ready", "topic": "",
         "started_at": "", "last_activity": ""},
        {"session_id": "s2", "pid": 5678, "cwd": "/tmp/PROJ-2-feature",
         "alive": True, "status": "ready", "topic": "",
         "started_at": "", "last_activity": ""},
    ]

    def _fake_discover(*_args: Any, **_kwargs: Any) -> list[dict]:
        return [dict(s) for s in template]

    monkeypatch.setattr("duct.session.discover_sessions", _fake_discover)
    monkeypatch.setattr("duct.api.discover_sessions", _fake_discover)
    return template


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Synthetic workspace with 5 tickets x 2 repos."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "config.yaml").write_text(
        "jira:\n  domain: example.atlassian.net\n  jql: ''\n"
        "github:\n  query: ''\n"
    )
    (ws / "WORKFLOW.md").write_text("\n")
    for i in range(1, 6):
        ticket = ws / f"PROJ-{i}-feature"
        ticket.mkdir()
        orch = ticket / "orchestrator"
        orch.mkdir()
        (orch / "TICKET.md").write_text(
            f"# PROJ-{i}: Feature {i}\n\n"
            "| Field | Value |\n"
            "|---|---|\n"
            "| Summary | Feature |\n"
            "| Status | In Progress |\n"
            "| Category | Active Development |\n"
            "| Priority | Medium |\n"
        )
        for repo_name in ("repoA", "repoB"):
            repo = ticket / repo_name
            repo.mkdir()
            (repo / ".git").mkdir()
    return ws


# --- Tests ----------------------------------------------------------------


def test_initial_load_does_not_duplicate_git_status(
    workspace: Path, fake_run: FakeRun, fake_sessions: list[dict],
) -> None:
    """Each repo should see exactly one ``git status --porcelain``.

    Before the fix, ``get_tickets`` and ``get_ticket_overviews`` both walked
    every repo separately, doubling the call count.
    """
    api.load_initial(workspace, adapter=None)
    cwds = fake_run.git_status_cwds()
    duplicates = [c for c in cwds if cwds.count(c) > 1]
    assert not duplicates, (
        f"git status was invoked twice for {sorted(set(duplicates))}; "
        f"all status calls: {cwds}"
    )
    # 5 tickets x 2 repos = 10. Anything above is regression.
    assert len(cwds) <= 10, f"too many git status calls: {len(cwds)}"


def test_initial_load_makes_no_osascript_calls(
    workspace: Path, fake_run: FakeRun, fake_sessions: list[dict],
) -> None:
    """The iTerm AppleScript fallback is forbidden on the startup hot path."""
    api.load_initial(workspace, adapter=None)
    osascript_calls = fake_run.calls_for("osascript")
    assert not osascript_calls, (
        f"osascript fired {len(osascript_calls)} times during initial load"
    )


def test_get_terminal_title_does_not_call_osascript(fake_run: FakeRun) -> None:
    """Direct check on the function: the iTerm AppleScript fallback was a
    3 s-per-call cliff for every alive non-wezterm session. Drop it."""
    from duct.terminal import get_terminal_title

    # Pick a tty that won't match any of the canned panes — historically
    # this is exactly when the AppleScript fallback fired.
    get_terminal_title("ttys999")

    assert not fake_run.calls_for("osascript"), (
        "get_terminal_title still falls back to osascript; remove the "
        "AppleScript block in duct.terminal.get_terminal_title"
    )


def test_initial_load_does_not_double_session_discovery(
    workspace: Path, fake_run: FakeRun, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``discover_sessions`` should run once per initial load, not per consumer."""
    counter = {"n": 0}

    def _counting_discover(*_args: Any, **_kwargs: Any) -> list[dict]:
        counter["n"] += 1
        return []

    monkeypatch.setattr("duct.session.discover_sessions", _counting_discover)
    monkeypatch.setattr("duct.api.discover_sessions", _counting_discover)
    api.load_initial(workspace, adapter=None)
    assert counter["n"] == 1, (
        f"discover_sessions ran {counter['n']} times; expected 1"
    )


def test_initial_load_with_wezterm_adapter_does_not_duplicate_pane_inspection(
    workspace: Path, fake_run: FakeRun, fake_sessions: list[dict],
) -> None:
    """When a wezterm adapter is passed, ``apply_overrides`` should only run
    once during the load — historically it ran once per consumer
    (``get_sessions`` and ``get_ticket_overviews`` both invoked it).

    Each apply_overrides invocation does one ``get_pane_text`` per alive
    session; with two alive sessions, two get-text calls is correct, four
    means apply_overrides ran twice.
    """
    from duct.terminal import WeztermAdapter

    adapter = WeztermAdapter()
    api.load_initial(workspace, adapter=adapter)

    get_text_calls = [
        c for c, _ in fake_run.calls
        if Path(c[0]).name == "wezterm" and "get-text" in c
    ]
    assert len(get_text_calls) <= 2, (
        f"expected at most 2 wezterm get-text calls (one per alive "
        f"session); got {len(get_text_calls)} — apply_overrides is "
        f"running multiple times"
    )


def test_initial_load_within_time_budget(
    workspace: Path, fake_sessions: list[dict],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wall-clock budget with 100 ms per subprocess call.

    Sequential 10 git status × 100 ms = 1.0 s. With parallel batching the
    fake_run delay floor for the git phase is ~100 ms regardless of repo
    count. 1.5 s leaves slack for parsing / file I/O on a loaded CI box
    while still failing if the parallelism regresses to sequential.
    """
    fake = FakeRun(delay_ms=100)
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(
        "duct.terminal.shutil.which",
        lambda binary: f"/usr/bin/{binary}",
    )
    _invalidate_pane_list_cache()
    start = time.monotonic()
    api.load_initial(workspace, adapter=None)
    elapsed = time.monotonic() - start
    assert elapsed < 1.5, f"initial load took {elapsed:.2f}s (budget 1.5s)"


def test_session_preview_served_from_pane_text_cache_makes_no_subprocess(
    fake_run: FakeRun, fake_sessions: list[dict],
) -> None:
    """The session preview should be served instantly from the pane-text
    cache that ``apply_overrides`` populates as a side effect. Hovering a
    session in the sidebar after a recent status refresh must not pay
    another ``wezterm cli get-text`` round trip.
    """
    from duct import pane_status
    from duct.terminal import WeztermAdapter

    adapter = WeztermAdapter()
    # Simulate a recent refresh: apply_overrides fetches pane text and
    # populates the cache.
    raw = [dict(s) for s in fake_sessions]
    pane_status.apply_overrides(raw, adapter=adapter)
    fake_run.calls.clear()

    text = api.get_session_preview(adapter, session_pid=1234)

    assert text is not None
    assert fake_run.calls == [], (
        f"preview must hit the pane-text cache, not subprocess. "
        f"calls fired: {[c[0] for c, _ in fake_run.calls]}"
    )


def test_session_preview_falls_back_to_one_wezterm_call_when_text_cache_cold(
    fake_run: FakeRun, fake_sessions: list[dict],
) -> None:
    """When the pane-text cache is empty (e.g. first preview after launch
    before any status refresh), preview falls back to a fresh fetch and
    should make exactly one ``wezterm cli get-text`` call — the pane-list
    cache should still be warm enough to skip a fresh ``cli list``.
    """
    from duct import pane_status
    from duct.terminal import WeztermAdapter, _wezterm_list_panes

    adapter = WeztermAdapter()
    pane_status._pane_text_cache.clear()
    _wezterm_list_panes()  # warm pane-list cache only
    fake_run.calls.clear()

    api.get_session_preview(adapter, session_pid=1234)

    wezterm_calls = fake_run.calls_for("wezterm")
    assert len(wezterm_calls) == 1, (
        f"expected 1 wezterm call (get-text) with warm pane-list cache "
        f"and cold text cache, got {len(wezterm_calls)}: "
        f"{[c[0] for c, _ in wezterm_calls]}"
    )
    assert "get-text" in wezterm_calls[0][0]


def test_get_sessions_uses_a_single_batched_ps_call(
    fake_run: FakeRun, fake_sessions: list[dict],
    workspace: Path,
) -> None:
    """``discover_sessions`` and ``apply_overrides`` historically each
    called ``get_tty(pid)`` once per alive session — N+N sequential ``ps``
    invocations, where each call is occasionally seconds slow on a loaded
    macOS system. The batched lookup coalesces these into one ``ps`` per
    session-load and shares the result across both consumers."""
    from duct.terminal import WeztermAdapter

    adapter = WeztermAdapter()
    api.get_sessions(workspace, adapter=adapter)

    ps_calls = fake_run.calls_for("ps")
    assert len(ps_calls) <= 1, (
        f"ps was called {len(ps_calls)} times for 2 alive sessions; "
        f"expected one batched call. Calls: {[c[0] for c in ps_calls]}"
    )


def test_dock_session_within_budget(
    workspace: Path, fake_sessions: list[dict],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dock should fit in 1 s with 200 ms-per-call latency.

    Cold cache: list (200 ms) + ps (200 ms) + split-pane (200 ms) = 600 ms.
    Warm cache: just split-pane = 200 ms.
    """
    from duct.terminal import WeztermAdapter

    fake = FakeRun(delay_ms=200)
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(
        "duct.terminal.shutil.which",
        lambda binary: f"/usr/bin/{binary}",
    )
    _invalidate_pane_list_cache()
    adapter = WeztermAdapter()

    start = time.monotonic()
    api.dock_session(adapter, tui_pane_id=10, session_pid=1234)
    elapsed = time.monotonic() - start

    assert elapsed < 1.0, f"dock took {elapsed:.2f}s (budget 1.0s)"


# --- Contention tests ----------------------------------------------------
#
# These tests reproduce the production regression where a 2 s background
# refresh fires N parallel ``wezterm cli get-text`` calls and queues the
# user's interactive preview/dock behind the entire batch on the wezterm
# IPC daemon. ``FakeRun(serialize=True)`` mirrors the daemon by holding a
# shared lock for the duration of each call's sleep, so concurrent callers
# compound rather than overlapping.


def test_apply_overrides_does_not_oversubscribe_wezterm_ipc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``apply_overrides`` must not have more than 2 ``wezterm cli get-text``
    calls in flight at once.

    The wezterm CLI's IPC daemon serialises requests on a single socket,
    so submitting 8 parallel calls doesn't speed anything up — it just
    queues 8 callers behind the daemon and starves any user-initiated
    call that arrives during the burst. The cap exists to leave headroom
    for an interactive preview/dock to land between background calls.
    """
    from duct import pane_status
    from duct.terminal import WeztermAdapter, _wezterm_list_panes

    pid_to_tty = {1000 + i: f"ttys{i:03d}" for i in range(6)}
    fake = FakeRun(delay_ms=50, serialize=True, pid_to_tty=pid_to_tty)
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(
        "duct.terminal.shutil.which",
        lambda binary: f"/usr/bin/{binary}",
    )
    _invalidate_pane_list_cache()
    pane_status._pane_text_cache.clear()

    adapter = WeztermAdapter()
    # Pre-warm pane-list cache so the burst we observe is purely get-text.
    _wezterm_list_panes()
    raw_sessions = [
        {"session_id": f"s{i}", "pid": pid, "cwd": f"/tmp/s{i}",
         "alive": True, "status": "ready", "topic": "",
         "started_at": "", "last_activity": "", "tty": tty}
        for i, (pid, tty) in enumerate(pid_to_tty.items())
    ]
    fake.calls.clear()
    # Reset the concurrency stat so we only measure the get-text burst.
    fake._max_concurrent = 0

    pane_status.apply_overrides(raw_sessions, adapter)

    assert fake.max_concurrent <= 2, (
        f"apply_overrides oversubscribes the wezterm IPC: max_concurrent="
        f"{fake.max_concurrent} (cap is 2). With {len(pid_to_tty)} sessions, "
        f"this means a user preview firing during a refresh queues behind "
        f"up to {fake.max_concurrent} subprocesses."
    )


def test_preview_serves_stale_cache_without_subprocess(
    fake_run: FakeRun,
) -> None:
    """``get_session_preview`` must serve any cached pane text instantly,
    even when the cache entry is older than the status-detection TTL.

    Re-hovering a session more than 10 s after the last refresh used to
    fall through to a fresh ``wezterm cli get-text`` call. Under load that
    call queues behind the next 2 s refresh batch, producing the user-
    visible 1–5 s preview lag. Stale text is fine for *preview* — the
    cache is only authoritative for status detection (a separate code path).
    """
    from duct import pane_status
    from duct.terminal import WeztermAdapter

    adapter = WeztermAdapter()
    # Seed the cache as if a refresh long ago captured this session.
    stale_text = "old captured pane content\n"
    pane_status._pane_text_cache[1234] = (
        time.monotonic() - (pane_status._PANE_TEXT_TTL + 5.0),
        stale_text,
    )
    fake_run.calls.clear()

    text = api.get_session_preview(adapter, session_pid=1234)

    assert text == stale_text, "preview should return the cached pane text"
    assert fake_run.calls == [], (
        f"preview must serve stale cache without subprocess. "
        f"Calls fired: {[c[0] for c, _ in fake_run.calls]}"
    )


def test_preview_during_concurrent_apply_overrides_stays_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The user's preview must not queue behind the entire apply_overrides
    burst.

    Setup: 6 alive sessions, 200 ms-per-call serialised wezterm IPC
    (mirrors the daemon's single-socket behaviour), pane-list cache
    pre-warmed so we observe only the get-text contention. We pre-populate
    the pane-text cache for pid 1000 so the user's preview is a cache hit
    that should return instantly regardless of what's in flight.

    With the parallelism bug, even a cache-hit preview can be delayed
    indirectly because ``find_pane_for_pid`` and other paths share the
    same wezterm CLI; the test specifically guarantees a hot cache so it
    targets only the cache-fast-path invariant. The corresponding cold-
    path improvement is bounded by the parallelism cap asserted in
    ``test_apply_overrides_does_not_oversubscribe_wezterm_ipc``.
    """
    from duct import pane_status
    from duct.terminal import WeztermAdapter, _wezterm_list_panes

    pid_to_tty = {1000 + i: f"ttys{i:03d}" for i in range(6)}
    fake = FakeRun(delay_ms=200, serialize=True, pid_to_tty=pid_to_tty)
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(
        "duct.terminal.shutil.which",
        lambda binary: f"/usr/bin/{binary}",
    )
    _invalidate_pane_list_cache()
    pane_status._pane_text_cache.clear()

    adapter = WeztermAdapter()
    _wezterm_list_panes()  # warm pane-list cache
    raw_sessions = [
        {"session_id": f"s{i}", "pid": pid, "cwd": f"/tmp/s{i}",
         "alive": True, "status": "ready", "topic": "",
         "started_at": "", "last_activity": "", "tty": tty}
        for i, (pid, tty) in enumerate(pid_to_tty.items())
    ]
    # Pre-populate the pane-text cache for the session we'll preview.
    pane_status._pane_text_cache[1000] = (
        time.monotonic(), "session output line 1\nline 2\n",
    )

    bg_done = threading.Event()

    def bg() -> None:
        try:
            pane_status.apply_overrides(raw_sessions, adapter)
        finally:
            bg_done.set()

    bg_thread = threading.Thread(target=bg)
    bg_thread.start()
    # Give the executor time to submit jobs into the lock queue.
    time.sleep(0.05)

    start = time.monotonic()
    text = api.get_session_preview(adapter, session_pid=1000)
    elapsed_ms = (time.monotonic() - start) * 1000.0

    bg_done.wait(timeout=10)
    bg_thread.join()

    assert text == "session output line 1\nline 2\n"
    assert elapsed_ms < 50, (
        f"preview took {elapsed_ms:.0f} ms with apply_overrides in flight; "
        f"a cache-hit preview must return in microseconds even under "
        f"concurrent refresh contention"
    )
