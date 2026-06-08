"""Tests for JiraActivityProvider — changelog + comment event extraction."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from duct.activity.providers.jira import JiraActivityProvider
from duct.config import WorkspaceConfig


def _cfg(tmp_path: Path) -> WorkspaceConfig:
    return WorkspaceConfig(root=tmp_path)


@pytest.fixture
def provider() -> JiraActivityProvider:
    return JiraActivityProvider(
        domain="example.atlassian.net",
        email="alice@example.com",
        token="tok",
    )


def _fetch(provider: JiraActivityProvider, tmp_path: Path) -> list:
    return list(
        provider.fetch(
            datetime(2026, 4, 21, tzinfo=timezone.utc),
            datetime(2026, 4, 22, tzinfo=timezone.utc),
            _cfg(tmp_path),
        )
    )


class TestJiraActivityProvider:
    def test_emits_status_change_for_self(
        self, httpx_mock: HTTPXMock, provider: JiraActivityProvider, tmp_path: Path
    ):
        # pytest-httpx consumes responses in registration order. The provider
        # issues /search/jql first; /myself is only touched lazily if the
        # email-based self-check fails.
        httpx_mock.add_response(
            json={
                "total": 1,
                "issues": [
                    {
                        "key": "FOO-1",
                        "fields": {"summary": "do thing", "comment": {"comments": []}},
                        "changelog": {
                            "histories": [
                                {
                                    "id": "hist-1",
                                    "created": "2026-04-21T10:00:00.000+0000",
                                    "author": {
                                        "emailAddress": "alice@example.com",
                                        "displayName": "Alice",
                                    },
                                    "items": [
                                        {
                                            "field": "status",
                                            "fromString": "To Do",
                                            "toString": "In Progress",
                                        }
                                    ],
                                }
                            ]
                        },
                    }
                ],
            }
        )

        events = _fetch(provider, tmp_path)
        assert len(events) == 1
        e = events[0]
        assert e.source == "jira"
        assert e.event_type == "status_change"
        assert e.ticket_key == "FOO-1"
        assert "To Do" in e.summary and "In Progress" in e.summary

    def test_ignores_other_users_history(
        self, httpx_mock: HTTPXMock, provider: JiraActivityProvider, tmp_path: Path
    ):
        # Other user has no email, so the provider falls back to /myself
        # to resolve the current user's accountId.
        httpx_mock.add_response(
            json={
                "total": 1,
                "issues": [
                    {
                        "key": "FOO-2",
                        "fields": {"summary": "x", "comment": {"comments": []}},
                        "changelog": {
                            "histories": [
                                {
                                    "id": "h",
                                    "created": "2026-04-21T10:00:00.000+0000",
                                    "author": {
                                        "displayName": "Bob",
                                        "accountId": "acct-bob",
                                    },
                                    "items": [
                                        {"field": "status", "fromString": "A", "toString": "B"}
                                    ],
                                }
                            ]
                        },
                    }
                ],
            }
        )
        httpx_mock.add_response(json={"accountId": "acct-alice"})

        events = _fetch(provider, tmp_path)
        assert events == []

    def test_emits_comment_events(
        self, httpx_mock: HTTPXMock, provider: JiraActivityProvider, tmp_path: Path
    ):
        httpx_mock.add_response(
            json={
                "total": 1,
                "issues": [
                    {
                        "key": "FOO-3",
                        "fields": {
                            "summary": "x",
                            "comment": {
                                "comments": [
                                    {
                                        "id": "c-1",
                                        "created": "2026-04-21T11:00:00.000+0000",
                                        "author": {
                                            "emailAddress": "alice@example.com",
                                            "accountId": "acct-alice",
                                        },
                                        "body": {
                                            "type": "doc",
                                            "version": 1,
                                            "content": [
                                                {
                                                    "type": "paragraph",
                                                    "content": [
                                                        {"type": "text", "text": "hello world"}
                                                    ],
                                                }
                                            ],
                                        },
                                    }
                                ]
                            },
                        },
                        "changelog": {"histories": []},
                    }
                ],
            }
        )

        events = _fetch(provider, tmp_path)
        assert len(events) == 1
        assert events[0].event_type == "comment"
        assert "hello world" in events[0].summary
