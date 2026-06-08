"""Realtime session status via wezterm pane-text inspection.

Claude Code's TUI renders distinctive anchors for each state (the
`(esc to interrupt)` spinner footer, tool-approval prompts, the persistent
plan-mode banner). The transcript layer in `session.py` can't observe these
directly — it only sees what Claude has serialised to the JSONL. Pane text is
the ground truth of what the user sees on screen, so we use it to correct the
working↔waiting axis and to source the orthogonal plan/default mode.

The transcript remains authoritative for `ready` and `terminated`; pane text
only ever overrides into `working` or `waiting`, never downgrades to `ready`.

The pane-text fetched here is also cached by ``pid`` (see
``_pane_text_cache``) so that the session preview UI can render instantly
from the most recent capture rather than paying another ``wezterm cli
get-text`` round trip — that capture is already at most 2 s old (the
session-refresh interval) and is therefore "live enough" for a preview.
"""

from __future__ import annotations

import concurrent.futures as cf
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from duct.terminal import TerminalAdapter


# CSI sequences (colors, cursor moves) and OSC sequences (title, links).
# We don't try to be exhaustive — SGR and OSC cover >99% of what wezterm
# get-text emits, and pattern matching runs against the stripped remainder.
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;:?]*[A-Za-z]"           # CSI: ESC [ params final_byte
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC: ESC ] ... BEL | ST
)

# Only inspect the tail of the pane — the spinner footer, approval prompts,
# and plan-mode banner all live within the last ~25 lines of the viewport
# (input box + mode bar + a few lines above). Anything higher is either
# scrollback pollution or response body, neither of which we should match.
_TAIL_LINES = 25

# Activity anchors (ordered: waiting > working, because approval prompts can
# briefly coexist with spinner text from the turn that triggered them).
#
# Claude Code's approval prompts have stable, multi-word phrasing that's
# unlikely to false-trigger on echoed shell output or message text:
_WAITING_ANCHORS = (
    "Do you want to proceed?",
    "Do you want to make this edit",
)

# Working spinner footer. Claude Code renders something like:
#     ✻ Sublimating… (2m 17s · ↓ 557 tokens · thought for 23s)
# where the glyph at the start is ANIMATED (cycles through ✻/·/etc.) and
# the verb rotates. The stable part is the ellipsis followed by parenthesised
# metadata that starts with a duration like "(0s", "(38s", "(2m". The "done"
# variant ("✻ Crunched for 14m 39s") has neither the ellipsis nor the paren,
# so it won't match.
_WORKING_RE = re.compile(r"…\s*\(\d+[ms]\b")

# Plan-mode banner. Claude Code renders a persistent footer like
# "⏸ plan mode on (shift+tab to cycle)" near the input box while plan mode
# is active. Match case-insensitively on the stable substring.
_PLAN_ANCHOR = "plan mode on"

# Override matrix: (transcript_status, pane_activity) -> final_status.
# Only these pairs override — any other combination leaves the transcript
# result untouched.
_OVERRIDE_MATRIX: dict[tuple[str, str], str] = {
    ("working", "waiting"): "waiting",
    ("waiting", "working"): "working",
    ("ready", "working"): "working",
}

# Opt-in diagnostic trace. Writes the stripped tail of every pane capture
# that fails to classify as "working" while the transcript says working or
# waiting — i.e. the exact condition that flaps the UI between states.
# Enable with: DUCT_PANE_STATUS_TRACE=1
_TRACE_ENV = "DUCT_PANE_STATUS_TRACE"
_TRACE_PATH = Path.home() / ".duct" / "pane-status-misses.log"

# Pane-text cache: pid -> (captured_at_monotonic, text). Populated as a
# side effect of `apply_overrides` so the preview UI can paint instantly
# without paying another `wezterm cli get-text` round trip. The TTL is
# generous because for a *preview* (vs. status detection) somewhat-stale
# text is fine — the user is reading recent output, not making decisions
# on per-second freshness.
_pane_text_cache: dict[int, tuple[float, str]] = {}
_PANE_TEXT_TTL = 10.0


def get_cached_pane_text(pid: int) -> str | None:
    """Return the most recent cached pane text for ``pid``, if fresh.

    Returns None if the cache is empty for this pid or if the entry is
    older than ``_PANE_TEXT_TTL`` seconds.
    """
    entry = _pane_text_cache.get(pid)
    if entry is None:
        return None
    captured_at, text = entry
    if time.monotonic() - captured_at > _PANE_TEXT_TTL:
        return None
    return text


def get_any_cached_pane_text(pid: int) -> str | None:
    """Return any cached pane text for ``pid``, regardless of age.

    Used by the preview path, where stale text is acceptable: the user
    is reading recent output, not making decisions on per-second
    freshness, and falling through to a fresh ``wezterm cli get-text``
    queues behind the next refresh batch and produces visible lag.
    Status detection (``get_cached_pane_text``) keeps the strict TTL.
    """
    entry = _pane_text_cache.get(pid)
    if entry is None:
        return None
    return entry[1]


def _trace_miss(pid: int, transcript_status: str, activity: str | None, text: str) -> None:
    try:
        _TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _TRACE_PATH.open("a", encoding="utf-8") as f:
            ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
            f.write(f"=== {ts} pid={pid} transcript={transcript_status} activity={activity!r} ===\n")
            f.write(_tail(text))
            f.write("\n")
    except OSError:
        # Tracing is best-effort — a broken log must never break status detection.
        pass


def strip_ansi(text: str) -> str:
    """Remove ANSI CSI and OSC escape sequences, leaving plain text intact."""
    return _ANSI_RE.sub("", text)


def _tail(text: str) -> str:
    """Return the last _TAIL_LINES lines of the stripped pane text."""
    lines = strip_ansi(text).splitlines()
    return "\n".join(lines[-_TAIL_LINES:])


def classify_activity(text: str) -> str | None:
    """Return `'working'`, `'waiting'`, or `None` when no anchor matches.

    Inspects only the last ~25 lines so stale spinner/approval text from
    earlier in the scrollback doesn't false-trigger.
    """
    tail = _tail(text)
    if any(anchor in tail for anchor in _WAITING_ANCHORS):
        return "waiting"
    if _WORKING_RE.search(tail):
        return "working"
    return None


def detect_mode(text: str) -> str:
    """Return `'plan'` if the persistent plan-mode banner is visible, else `'default'`."""
    tail = _tail(text).lower()
    return "plan" if _PLAN_ANCHOR in tail else "default"


def _should_override(transcript_status: str, pane_activity: str) -> bool:
    return (transcript_status, pane_activity) in _OVERRIDE_MATRIX


def apply_overrides(
    sessions: list[dict],
    adapter: "TerminalAdapter | None",
    *,
    max_workers: int = 2,
    timeout_s: float = 1.2,
) -> None:
    """Enrich `sessions` in place with pane-text-derived `mode` and status overrides.

    For every alive session with a resolvable wezterm pane:
      1. Capture the pane text once.
      2. Set `mode` from `detect_mode(text)` (plan | default).
      3. If `classify_activity(text)` returns a state that the override matrix
         accepts against the session's current `status`, replace `status` with it.

    On any failure (no adapter, wezterm missing, timeout, pane not found),
    the session keeps its transcript-derived status and gets `mode=""`.
    """
    # Set a default so SessionInfo.mode is always populated on the no-op path.
    for s in sessions:
        s.setdefault("mode", "")

    if adapter is None or getattr(adapter, "name", "") != "wezterm":
        return

    alive = [s for s in sessions if s.get("alive") and s.get("pid")]
    if not alive:
        return

    # Local import to avoid a cycle: terminal.py may import from here in future.
    from duct.terminal import _wezterm_list_panes, get_ttys

    panes = _wezterm_list_panes()
    if not panes:
        return

    # Build a (tty_name suffix -> pane_id) map from a single list call.
    pane_by_tty: dict[str, int] = {}
    for p in panes:
        name = p.get("tty_name") or ""
        pid = p.get("pane_id")
        if name and pid is not None:
            pane_by_tty[name] = pid

    # Reuse ttys discovered by discover_sessions when present; batch-fetch
    # any that are missing so we never spawn N parallel ``ps`` subprocesses.
    missing_pids = [s["pid"] for s in alive if not s.get("tty")]
    if missing_pids:
        fresh = get_ttys(missing_pids)
        for s in alive:
            if not s.get("tty"):
                tty = fresh.get(s["pid"])
                if tty:
                    s["tty"] = tty

    pid_to_pane: dict[int, int] = {}
    for s in alive:
        tty = s.get("tty")
        if not tty:
            continue
        for tty_name, pane_id in pane_by_tty.items():
            if tty_name.endswith(tty):
                pid_to_pane[s["pid"]] = pane_id
                break

    if not pid_to_pane:
        return

    # Parallel get_pane_text. We bound the *batch* wait at timeout_s so a
    # single slow subprocess can't blow past the poll interval; any futures
    # still outstanding when we time out simply don't contribute overrides
    # to the status decision.
    #
    # Late futures (those that finish AFTER timeout_s but before the
    # ThreadPoolExecutor exit blocks for cleanup) still publish into the
    # preview cache via `add_done_callback`. A preview hovering over a
    # session 1 s after a slow refresh thus benefits from the capture
    # that just landed, even though it didn't make the status window.
    #
    # The default cap (max_workers=2) is deliberately small. The wezterm
    # CLI's IPC daemon serialises requests on a single socket — submitting
    # 8 parallel calls doesn't speed anything up, it just queues 8 callers
    # and starves any user-initiated preview/dock that arrives during the
    # burst. Two workers leave headroom for the user to interleave.
    workers = max(1, min(max_workers, len(pid_to_pane)))
    pid_to_text: dict[int, str | None] = {}

    def _publish_to_cache(pid: int, future) -> None:
        try:
            text = future.result()
        except Exception:
            return
        if text:
            _pane_text_cache[pid] = (time.monotonic(), text)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        future_to_pid = {
            ex.submit(adapter.get_pane_text, pane_id): pid
            for pid, pane_id in pid_to_pane.items()
        }
        for future, pid in future_to_pid.items():
            future.add_done_callback(
                lambda f, p=pid: _publish_to_cache(p, f),
            )
        done, not_done = cf.wait(future_to_pid.keys(), timeout=timeout_s)
        for future in done:
            pid = future_to_pid[future]
            try:
                pid_to_text[pid] = future.result()
            except Exception:
                pid_to_text[pid] = None
        for future in not_done:
            future.cancel()

    trace_enabled = os.environ.get(_TRACE_ENV) == "1"

    # The pane-text cache is populated by `_publish_to_cache` via
    # `add_done_callback`, including for futures that finish past the
    # status-decision timeout window — no additional snapshot needed here.

    for s in sessions:
        pid = s.get("pid")
        if pid is None:
            continue
        text = pid_to_text.get(pid)
        if not text:
            continue
        s["mode"] = detect_mode(text)
        activity = classify_activity(text)
        transcript_status = s.get("status", "")
        if trace_enabled and activity != "working" and transcript_status in ("working", "waiting"):
            _trace_miss(pid, transcript_status, activity, text)
        if activity and _should_override(transcript_status, activity):
            s["status"] = activity
