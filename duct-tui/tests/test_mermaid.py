"""Tests for mermaid segment splitting and renderer cache behaviour."""

from __future__ import annotations

import hashlib

import pytest

from duct_tui import mermaid
from duct_tui.widgets.artifact_view import _split_segments


# -- Splitter --

def test_split_no_fence_returns_single_md_segment():
    segments = _split_segments("just prose\n\nmore prose")
    assert segments == [("md", "just prose\n\nmore prose")]


def test_split_extracts_single_mermaid_block():
    content = "intro\n\n```mermaid\ngraph TD\n  A --> B\n```\n\noutro"
    segments = _split_segments(content)
    assert segments == [
        ("md", "intro\n"),
        ("mermaid", "graph TD\n  A --> B"),
        ("md", "\noutro"),
    ]


def test_split_handles_multiple_mermaid_blocks():
    content = (
        "```mermaid\nA\n```\n"
        "middle\n"
        "```mermaid\nB\n```"
    )
    segments = _split_segments(content)
    kinds = [k for k, _ in segments]
    assert kinds == ["mermaid", "md", "mermaid"]
    assert segments[0][1] == "A"
    assert segments[2][1] == "B"


def test_split_mermaid_at_start_and_end():
    content = "```mermaid\nonly\n```"
    assert _split_segments(content) == [("mermaid", "only")]


def test_split_unclosed_fence_stays_as_markdown():
    content = "```mermaid\ngraph TD\n  A --> B\n"
    segments = _split_segments(content)
    assert len(segments) == 1
    assert segments[0][0] == "md"
    assert "```mermaid" in segments[0][1]


def test_split_preserves_inner_backticks():
    # Single backticks inside the mermaid source must not close the fence.
    content = "```mermaid\nclassDiagram\n  class `Foo`\n```"
    segments = _split_segments(content)
    assert segments == [("mermaid", "classDiagram\n  class `Foo`")]


# -- Cache path --

def test_cache_path_is_stable_for_same_source():
    src = "graph TD\nA --> B"
    assert mermaid.cache_path(src) == mermaid.cache_path(src)


def test_cache_path_differs_for_different_sources():
    assert mermaid.cache_path("A") != mermaid.cache_path("B")


def test_cache_path_uses_versioned_sha256_of_source():
    src = "graph TD"
    keyed = f"{mermaid._RENDER_VERSION}:{src}".encode("utf-8")
    expected = hashlib.sha256(keyed).hexdigest()
    assert mermaid.cache_path(src).name == f"{expected}.png"


# -- Renderer fallback --

def test_render_returns_none_when_mmdc_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(mermaid.shutil, "which", lambda _name: None)
    monkeypatch.setattr(mermaid, "CACHE_DIR", tmp_path / "cache")
    assert mermaid.render_to_png("graph TD\nA --> B") is None


def test_render_hits_cache_without_invoking_mmdc(monkeypatch, tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(mermaid, "CACHE_DIR", cache)
    src = "graph TD\nA --> B"
    cached = mermaid.cache_path(src)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    def fail(*_a, **_kw):
        raise AssertionError("mmdc must not run on cache hit")

    monkeypatch.setattr(mermaid.subprocess, "run", fail)
    monkeypatch.setattr(mermaid.shutil, "which", lambda _name: "/usr/bin/mmdc")

    assert mermaid.render_to_png(src) == cached
