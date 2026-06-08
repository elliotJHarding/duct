"""Rich rendering for cached avatars.

The network + on-disk cache lives in :mod:`duct.avatar_cache` (duct-cli) so the
daemon and the TUI share one cache. This module is the TUI's rendering half:
it asks the cache for a path and turns it into a halfcell image, falling back
to a coloured initials badge while the download is still in flight.
"""

from __future__ import annotations

import hashlib
from typing import Callable

from rich.text import Text

from duct.avatar_cache import ensure_avatar

# Avatars are tiny (~80x80 PNGs). Halfcell mode renders 4x2 cells nicely.
_AVATAR_CELL_WIDTH = 4
_AVATAR_CELL_HEIGHT = 2


def render_avatar(login: str | None, url: str | None, on_ready: Callable[[], None]):
    """Return a Rich renderable suitable for the avatar column.

    Prefers a halfcell image; falls back to a coloured initials badge when
    the PNG hasn't downloaded yet or isn't available.
    """
    path = ensure_avatar(login, url, on_ready)
    if path is not None:
        try:
            from textual_image.renderable import HalfcellImage

            return HalfcellImage(
                str(path),
                width=_AVATAR_CELL_WIDTH,
                height=_AVATAR_CELL_HEIGHT,
            )
        except Exception:
            # PIL/render failure — fall through to initials.
            pass
    return _initials_badge(login)


def _initials_badge(login: str | None) -> Text:
    """Tiny coloured initials badge used when no avatar image is available."""
    if not login:
        return Text("  · \n  · ", style="dim")
    initials = (login[:2] or "  ").upper()
    # Stable per-login colour so badges feel consistent across refreshes.
    hue = int(hashlib.md5(login.encode("utf-8")).hexdigest()[:2], 16) % 6
    palette = ["red", "green", "yellow", "blue", "magenta", "cyan"]
    style = f"bold white on {palette[hue]}"
    # Two-line block so it visually aligns with a 2-row halfcell avatar.
    text = Text()
    text.append(f" {initials} ", style=style)
    text.append("\n")
    text.append("    ", style=style)
    return text
