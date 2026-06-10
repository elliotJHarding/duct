"""Tests for workspace_render — the per-repo card layout.

Cards are rendered through a Rich Console and asserted on the captured plain
text, so the checks are independent of style metadata.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from duct.models import RepoStatus
from duct_tui.icons import UNICODE
from duct_tui.widgets.workspace_render import render_repo_card


def _render(repo: RepoStatus, *, width: int = 60) -> str:
    console = Console(width=width, no_color=True)
    with console.capture() as cap:
        console.print(render_repo_card(repo, UNICODE, width=width))
    return cap.get()


def _repo(**kwargs) -> RepoStatus:
    defaults = dict(
        name="ice-claims",
        path=Path("/tmp/ice-claims"),
        branch="feature/KAM-1856-create",
        dirty=True,
        uncommitted_changes=2,
        recent_commits=[
            "abc1234 Wire up the importer",
            "def5678 Add changelog table",
        ],
    )
    defaults.update(kwargs)
    return RepoStatus(**defaults)


def test_card_shows_name_branch_and_commits():
    out = _render(_repo())
    assert "ice-claims" in out
    assert "feature/KAM-1856-create" in out
    assert "Wire up the importer" in out  # commit subject, hash stripped
    assert "Add changelog table" in out
    assert "2 changes" in out  # dirtiness summary stays in the title


def test_card_caps_commits():
    commits = [f"hash{i:04d} commit {i}" for i in range(10)]
    out = _render(_repo(recent_commits=commits))
    assert "commit 0" in out
    assert "commit 4" in out
    assert "commit 5" not in out  # capped at 5


def test_clean_repo_with_no_commits():
    out = _render(_repo(dirty=False, uncommitted_changes=0, recent_commits=[]))
    assert "clean" in out
    assert "no commits on branch" in out
