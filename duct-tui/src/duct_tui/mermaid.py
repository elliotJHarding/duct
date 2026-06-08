"""Mermaid diagram renderer -- shells out to `mmdc` and caches PNG output.

The caller gives us mermaid source text; we return a PNG path on disk. Cache key
is a sha256 of the source, so re-renders of unchanged diagrams are free (mmdc
cold-start is 1-3s because it boots puppeteer).

`mmdc` (mermaid-cli) is an optional Node.js dependency. When it's missing or
rendering fails, `render_to_png` returns None -- callers should fall back to
showing the source as a code block.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path


CACHE_DIR = Path.home() / ".cache" / "duct" / "mermaid"

# Bump when MMDC_ARGS change so old cached PNGs get regenerated.
_RENDER_VERSION = "v4"
_MMDC_ARGS = ("-b", "transparent", "-w", "1000")


def is_available() -> bool:
    """True if `mmdc` is on PATH."""
    return shutil.which("mmdc") is not None


def cache_path(source: str) -> Path:
    """PNG path for a given mermaid source -- stable across invocations."""
    keyed = f"{_RENDER_VERSION}:{source}".encode("utf-8")
    digest = hashlib.sha256(keyed).hexdigest()
    return CACHE_DIR / f"{digest}.png"


def render_to_png(source: str, *, timeout: float = 30.0) -> Path | None:
    """Render mermaid source to a cached PNG. Returns None on any failure."""
    target = cache_path(source)
    if target.exists():
        return target

    mmdc = shutil.which("mmdc")
    if mmdc is None:
        return None

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            [mmdc, "-i", "-", "-o", str(target), *_MMDC_ARGS],
            input=source,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0 or not target.exists():
        return None
    return target
