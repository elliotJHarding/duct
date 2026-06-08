"""On-disk avatar cache with background prefetch.

Stores downloaded avatars at ``~/.cache/duct/avatars/{login}.png`` and hands
back a path once present. Network I/O happens in a daemon thread so callers
never block; until the download lands, callers fall back to their own
placeholder (an initials badge in the TUI, the brand icon in notifications).

Lives in duct-cli so both the TUI (avatar column rendering) and the daemon
(notification ``content_image``) resolve avatars through the same cache.
"""

from __future__ import annotations

import hashlib
import threading
import urllib.request
from pathlib import Path
from typing import Callable


def _cache_root() -> Path:
    root = Path.home() / ".cache" / "duct" / "avatars"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cache_path(login: str) -> Path:
    # Logins are already filename-safe, but hash for robustness against odd chars.
    safe = hashlib.sha1(login.encode("utf-8")).hexdigest()[:16]
    return _cache_root() / f"{login}-{safe}.png"


# Track in-flight downloads so we don't spawn duplicate threads per login.
_in_flight: set[str] = set()
_lock = threading.Lock()


def _download_avatar(login: str, url: str, on_done: Callable[[], None]) -> None:
    """Thread target: fetch avatar to disk, invoke ``on_done`` on success."""
    try:
        path = _cache_path(login)
        request = urllib.request.Request(url, headers={"User-Agent": "duct/1.0"})
        with urllib.request.urlopen(request, timeout=10) as response:
            data = response.read()
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)
        on_done()
    except Exception:
        # Silently drop failures — the caller's fallback stays in place.
        pass
    finally:
        with _lock:
            _in_flight.discard(login)


def ensure_avatar(
    login: str | None,
    url: str | None,
    on_ready: Callable[[], None],
) -> Path | None:
    """Return a cached avatar path if present; otherwise kick off a download.

    ``on_ready`` is invoked from the worker thread after a successful download,
    so the caller can trigger a re-render. When no URL is available, no work is
    scheduled and ``None`` is returned.
    """
    if not login:
        return None
    path = _cache_path(login)
    if path.exists() and path.stat().st_size > 0:
        return path
    if not url:
        return None
    with _lock:
        if login in _in_flight:
            return None
        _in_flight.add(login)
    thread = threading.Thread(
        target=_download_avatar,
        args=(login, url, on_ready),
        daemon=True,
        name=f"avatar-{login}",
    )
    thread.start()
    return None
