"""Tests for GitHubActivityProvider translation of /users/{self}/events payloads."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from pytest_httpx import HTTPXMock

from duct.activity.providers.github import GitHubActivityProvider
from duct.config import WorkspaceConfig


def _cfg(tmp_path: Path) -> WorkspaceConfig:
    return WorkspaceConfig(root=tmp_path)


@pytest.fixture
def provider() -> GitHubActivityProvider:
    return GitHubActivityProvider(token="fake-token", username="alice")


class TestTranslate:
    def test_push_event_emits_one_per_commit(
        self, httpx_mock: HTTPXMock, provider: GitHubActivityProvider, tmp_path: Path
    ):
        httpx_mock.add_response(
            method="GET",
            url="https://api.github.com/users/alice/events?per_page=100&page=1",
            json=[
                {
                    "id": "1001",
                    "type": "PushEvent",
                    "created_at": "2026-04-21T09:00:00Z",
                    "actor": {"login": "alice"},
                    "repo": {"name": "acme/backend"},
                    "payload": {
                        "ref": "refs/heads/feature/ERSC-1278",
                        "commits": [
                            {"sha": "abcdef1234567890", "message": "ERSC-1278: wip"},
                            {"sha": "abcdef9876543210", "message": "ERSC-1278: polish"},
                        ],
                    },
                }
            ],
        )
        httpx_mock.add_response(
            method="GET",
            url="https://api.github.com/users/alice/events?per_page=100&page=2",
            json=[],
        )

        events = list(
            provider.fetch(
                datetime(2026, 4, 21, tzinfo=timezone.utc),
                datetime(2026, 4, 22, tzinfo=timezone.utc),
                _cfg(tmp_path),
            )
        )
        assert len(events) == 2
        assert all(e.source == "github" for e in events)
        assert all(e.event_type == "commit_pushed" for e in events)
        assert all(e.ticket_key == "ERSC-1278" for e in events)
        assert {e.detail["sha"] for e in events} == {
            "abcdef1234567890",
            "abcdef9876543210",
        }

    def test_pr_opened_event(
        self, httpx_mock: HTTPXMock, provider: GitHubActivityProvider, tmp_path: Path
    ):
        httpx_mock.add_response(
            method="GET",
            url="https://api.github.com/users/alice/events?per_page=100&page=1",
            json=[
                {
                    "id": "2002",
                    "type": "PullRequestEvent",
                    "created_at": "2026-04-21T10:00:00Z",
                    "actor": {"login": "alice"},
                    "repo": {"name": "acme/backend"},
                    "payload": {
                        "action": "opened",
                        "pull_request": {
                            "number": 42,
                            "title": "ERSC-1278: fix auth",
                            "merged": False,
                            "html_url": "https://github.com/acme/backend/pull/42",
                            "head": {"ref": "feature/ERSC-1278"},
                        },
                    },
                }
            ],
        )
        httpx_mock.add_response(
            method="GET",
            url="https://api.github.com/users/alice/events?per_page=100&page=2",
            json=[],
        )

        events = list(
            provider.fetch(
                datetime(2026, 4, 21, tzinfo=timezone.utc),
                datetime(2026, 4, 22, tzinfo=timezone.utc),
                _cfg(tmp_path),
            )
        )
        assert len(events) == 1
        e = events[0]
        assert e.event_type == "pr_opened"
        assert e.ticket_key == "ERSC-1278"
        assert "#42" in e.summary
        assert e.url == "https://github.com/acme/backend/pull/42"

    def test_pr_merged_vs_closed(
        self, httpx_mock: HTTPXMock, provider: GitHubActivityProvider, tmp_path: Path
    ):
        httpx_mock.add_response(
            method="GET",
            url="https://api.github.com/users/alice/events?per_page=100&page=1",
            json=[
                {
                    "id": "3003",
                    "type": "PullRequestEvent",
                    "created_at": "2026-04-21T11:00:00Z",
                    "actor": {"login": "alice"},
                    "repo": {"name": "acme/backend"},
                    "payload": {
                        "action": "closed",
                        "pull_request": {
                            "number": 43,
                            "title": "wip",
                            "merged": True,
                            "head": {"ref": "foo"},
                        },
                    },
                }
            ],
        )
        httpx_mock.add_response(
            method="GET",
            url="https://api.github.com/users/alice/events?per_page=100&page=2",
            json=[],
        )

        events = list(
            provider.fetch(
                datetime(2026, 4, 21, tzinfo=timezone.utc),
                datetime(2026, 4, 22, tzinfo=timezone.utc),
                _cfg(tmp_path),
            )
        )
        assert events[0].event_type == "pr_merged"

    def test_pr_review_event(
        self, httpx_mock: HTTPXMock, provider: GitHubActivityProvider, tmp_path: Path
    ):
        httpx_mock.add_response(
            method="GET",
            url="https://api.github.com/users/alice/events?per_page=100&page=1",
            json=[
                {
                    "id": "4004",
                    "type": "PullRequestReviewEvent",
                    "created_at": "2026-04-21T12:00:00Z",
                    "actor": {"login": "alice"},
                    "repo": {"name": "acme/backend"},
                    "payload": {
                        "review": {"state": "APPROVED"},
                        "pull_request": {
                            "number": 44,
                            "title": "BAR-7 change",
                            "head": {"ref": "feature/BAR-7"},
                            "html_url": "https://github.com/acme/backend/pull/44",
                        },
                    },
                }
            ],
        )
        httpx_mock.add_response(
            method="GET",
            url="https://api.github.com/users/alice/events?per_page=100&page=2",
            json=[],
        )

        events = list(
            provider.fetch(
                datetime(2026, 4, 21, tzinfo=timezone.utc),
                datetime(2026, 4, 22, tzinfo=timezone.utc),
                _cfg(tmp_path),
            )
        )
        assert events[0].event_type == "pr_review"
        assert "approved" in events[0].summary.lower()
        assert events[0].ticket_key == "BAR-7"

    def test_issue_comment_vs_pr_comment(
        self, httpx_mock: HTTPXMock, provider: GitHubActivityProvider, tmp_path: Path
    ):
        httpx_mock.add_response(
            method="GET",
            url="https://api.github.com/users/alice/events?per_page=100&page=1",
            json=[
                {
                    "id": "5005",
                    "type": "IssueCommentEvent",
                    "created_at": "2026-04-21T13:00:00Z",
                    "actor": {"login": "alice"},
                    "repo": {"name": "acme/backend"},
                    "payload": {
                        "issue": {
                            "number": 7,
                            "title": "plain issue",
                            "html_url": "https://github.com/acme/backend/issues/7",
                        },
                        "comment": {"id": 88, "body": "thoughts", "html_url": "..."},
                    },
                },
                {
                    "id": "5006",
                    "type": "IssueCommentEvent",
                    "created_at": "2026-04-21T13:10:00Z",
                    "actor": {"login": "alice"},
                    "repo": {"name": "acme/backend"},
                    "payload": {
                        "issue": {
                            "number": 8,
                            "title": "PR title",
                            "pull_request": {"url": "https://api.github.com/repos/acme/backend/pulls/8"},
                            "html_url": "https://github.com/acme/backend/pull/8",
                        },
                        "comment": {"id": 89, "body": "lgtm", "html_url": "..."},
                    },
                },
            ],
        )
        httpx_mock.add_response(
            method="GET",
            url="https://api.github.com/users/alice/events?per_page=100&page=2",
            json=[],
        )

        events = list(
            provider.fetch(
                datetime(2026, 4, 21, tzinfo=timezone.utc),
                datetime(2026, 4, 22, tzinfo=timezone.utc),
                _cfg(tmp_path),
            )
        )
        kinds = {e.event_type for e in events}
        assert "issue_comment" in kinds
        assert "pr_comment" in kinds

    def test_event_outside_window_ends_paging(
        self, httpx_mock: HTTPXMock, provider: GitHubActivityProvider, tmp_path: Path
    ):
        # Single page where the oldest event predates `since` → no second
        # request should be issued.
        httpx_mock.add_response(
            method="GET",
            url="https://api.github.com/users/alice/events?per_page=100&page=1",
            json=[
                {
                    "id": "1",
                    "type": "PushEvent",
                    "created_at": "2020-01-01T00:00:00Z",
                    "actor": {"login": "alice"},
                    "repo": {"name": "acme/backend"},
                    "payload": {"ref": "refs/heads/main", "commits": []},
                }
            ],
        )

        events = list(
            provider.fetch(
                datetime(2026, 4, 21, tzinfo=timezone.utc),
                datetime(2026, 4, 22, tzinfo=timezone.utc),
                _cfg(tmp_path),
            )
        )
        assert events == []
