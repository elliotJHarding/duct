"""Tests for the Jira sync source."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from duct.config import SandboxConfig
from duct.exceptions import AuthError
from duct.models import Comment, Ticket
from duct.sync.jira import JiraSync, _status_category

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.fixture
def search_response() -> dict:
    return json.loads((FIXTURES / "jira_search_response.json").read_text())


@pytest.fixture
def transitions_response() -> dict:
    return json.loads((FIXTURES / "jira_transitions_response.json").read_text())


@pytest.fixture
def jira() -> JiraSync:
    return JiraSync(
        domain="jira.example.com",
        email="user@example.com",
        token="fake-token",
        jql="assignee = currentUser()",
    )


@pytest.fixture
def jira_with_sandbox() -> JiraSync:
    return JiraSync(
        domain="jira.example.com",
        email="user@example.com",
        token="fake-token",
        jql="assignee = currentUser()",
        sandbox=SandboxConfig(),
    )


# ---------------------------------------------------------------------------
# Construction / auth validation
# ---------------------------------------------------------------------------


class TestJiraSyncInit:
    def test_missing_domain_raises(self):
        with pytest.raises(AuthError, match="domain"):
            JiraSync(domain="", email="a@b.com", token="tok", jql="x")

    def test_missing_email_raises(self):
        with pytest.raises(AuthError, match="EMAIL"):
            JiraSync(domain="jira.example.com", email="", token="tok", jql="x")

    def test_missing_token_raises(self):
        with pytest.raises(AuthError, match="TOKEN"):
            JiraSync(domain="jira.example.com", email="a@b.com", token="", jql="x")

    def test_valid_construction(self, jira: JiraSync):
        assert jira.name == "jira"


# ---------------------------------------------------------------------------
# Status category mapping
# ---------------------------------------------------------------------------


class TestStatusCategory:
    def test_in_progress(self):
        assert _status_category("In Progress") == "Active Development"

    def test_to_do(self):
        assert _status_category("To Do") == "Pre-Development"

    def test_done(self):
        assert _status_category("Done") == "Done"

    def test_testing(self):
        assert _status_category("Testing") == "In Test"

    def test_ready_to_deploy(self):
        assert _status_category("Ready to Deploy") == "Awaiting Action"

    def test_unknown_defaults_to_pre_development(self):
        assert _status_category("Some Unknown Status") == "Pre-Development"

    def test_case_insensitive(self):
        assert _status_category("in progress") == "Active Development"
        assert _status_category("IN PROGRESS") == "Active Development"


# ---------------------------------------------------------------------------
# _extract_ticket
# ---------------------------------------------------------------------------


class TestExtractTicket:
    def test_parses_full_issue(self, jira: JiraSync, search_response: dict):
        issue = search_response["issues"][0]
        epic_map = {"PROJ-101": "PROJ-50"}
        ticket = jira._extract_ticket(issue, epic_map)

        assert ticket.key == "PROJ-101"
        assert ticket.summary == "Implement user authentication"
        assert ticket.status == "In Progress"
        assert ticket.category == "Active Development"
        assert ticket.priority == "High"
        assert ticket.issue_type == "Story"
        assert ticket.assignee == "Alice Smith"
        assert ticket.epic_key == "PROJ-50"
        assert ticket.sprint == "Sprint 5"
        assert ticket.fix_versions == ["1.2.0"]
        assert ticket.components == ["Backend"]
        assert ticket.labels == ["security", "mvp"]
        assert "OAuth2" in ticket.description
        assert ticket.customer_name == "ERS"
        assert ticket.url == "https://jira.example.com/browse/PROJ-101"

    def test_customer_name_none_when_field_null(self, jira: JiraSync, search_response: dict):
        issue = search_response["issues"][1]
        ticket = jira._extract_ticket(issue, {})
        assert ticket.customer_name is None

    def test_unassigned_when_null(self, jira: JiraSync, search_response: dict):
        issue = search_response["issues"][1]
        ticket = jira._extract_ticket(issue, {})
        assert ticket.assignee == "Unassigned"

    def test_no_description(self, jira: JiraSync, search_response: dict):
        issue = search_response["issues"][1]
        ticket = jira._extract_ticket(issue, {})
        assert ticket.description == ""

    def test_comments_sorted_newest_first(self, jira: JiraSync, search_response: dict):
        issue = search_response["issues"][0]
        ticket = jira._extract_ticket(issue, {})
        assert len(ticket.comments) == 2
        # Newest comment (2026-03-16) should come first.
        assert ticket.comments[0].author == "Alice Smith"
        assert ticket.comments[1].author == "Bob Jones"

    def test_empty_comments(self, jira: JiraSync, search_response: dict):
        issue = search_response["issues"][1]
        ticket = jira._extract_ticket(issue, {})
        assert ticket.comments == []


# ---------------------------------------------------------------------------
# Epic resolution
# ---------------------------------------------------------------------------


class TestEpicResolution:
    def test_customfield_10014_takes_priority(self, jira: JiraSync):
        fields = {
            "customfield_10014": "EPIC-1",
            "parent": {
                "key": "EPIC-2",
                "fields": {"issuetype": {"name": "Epic"}},
            },
        }
        assert jira._resolve_epic("PROJ-10", fields) == "EPIC-1"

    def test_parent_epic_fallback(self, jira: JiraSync):
        fields = {
            "customfield_10014": None,
            "parent": {
                "key": "EPIC-2",
                "fields": {"issuetype": {"name": "Epic"}},
            },
        }
        assert jira._resolve_epic("PROJ-10", fields) == "EPIC-2"

    def test_parent_non_epic_ignored(self, jira: JiraSync):
        fields = {
            "customfield_10014": None,
            "parent": {
                "key": "STORY-5",
                "fields": {"issuetype": {"name": "Story"}},
            },
        }
        assert jira._resolve_epic("PROJ-10", fields) is None

    def test_self_reference_guard_customfield(self, jira: JiraSync):
        fields = {"customfield_10014": "PROJ-10", "parent": None}
        assert jira._resolve_epic("PROJ-10", fields) is None

    def test_self_reference_guard_parent(self, jira: JiraSync):
        fields = {
            "customfield_10014": None,
            "parent": {
                "key": "PROJ-10",
                "fields": {"issuetype": {"name": "Epic"}},
            },
        }
        assert jira._resolve_epic("PROJ-10", fields) is None

    def test_no_epic(self, jira: JiraSync):
        fields = {"customfield_10014": None, "parent": None}
        assert jira._resolve_epic("PROJ-10", fields) is None


# ---------------------------------------------------------------------------
# _search_issues (HTTP mocking)
# ---------------------------------------------------------------------------


class TestSearchIssues:
    def test_single_page(self, jira: JiraSync, httpx_mock, search_response: dict):
        httpx_mock.add_response(
            url=httpx.URL(
                "https://jira.example.com/rest/api/3/search/jql",
                params={
                    "jql": "assignee = currentUser()",
                    "fields": str(
                        "summary,status,priority,issuetype,assignee,project,parent,"
                        "customfield_10014,customfield_10020,customfield_11704,fixVersions,components,"
                        "labels,comment,description"
                    ),
                    "startAt": "0",
                    "maxResults": "50",
                },
            ),
            json=search_response,
        )

        issues = jira._search_issues()
        assert len(issues) == 2
        assert issues[0]["key"] == "PROJ-101"
        assert issues[1]["key"] == "PROJ-102"

    def test_pagination(self, jira: JiraSync, httpx_mock):
        page1 = {
            "startAt": 0,
            "maxResults": 1,
            "total": 2,
            "issues": [
                {
                    "key": "PROJ-100",
                    "id": "10100",
                    "fields": {
                        "summary": "First page ticket",
                        "status": {"name": "To Do"},
                        "priority": {"name": "High"},
                        "issuetype": {"name": "Task"},
                        "assignee": None,
                        "project": {"key": "PROJ"},
                        "parent": None,
                        "customfield_10014": None,
                        "customfield_10020": None,
                        "fixVersions": [],
                        "components": [],
                        "labels": [],
                        "description": None,
                        "comment": {"comments": []},
                    },
                }
            ],
        }
        page2 = json.loads((FIXTURES / "jira_search_page2.json").read_text())

        # First request (startAt=0).
        httpx_mock.add_response(json=page1)
        # Second request (startAt=1).
        httpx_mock.add_response(json=page2)

        issues = jira._search_issues()
        assert len(issues) == 2
        assert issues[0]["key"] == "PROJ-100"
        assert issues[1]["key"] == "PROJ-200"

    def test_auth_failure_raises(self, jira: JiraSync, httpx_mock):
        httpx_mock.add_response(status_code=401, text="Unauthorized")

        with pytest.raises(AuthError, match="401"):
            jira._search_issues()

    def test_forbidden_raises(self, jira: JiraSync, httpx_mock):
        httpx_mock.add_response(status_code=403, text="Forbidden")

        with pytest.raises(AuthError, match="403"):
            jira._search_issues()

    def test_server_error_raises(self, jira: JiraSync, httpx_mock):
        httpx_mock.add_response(status_code=500, text="Internal Server Error")

        from duct.exceptions import SyncError

        with pytest.raises(SyncError, match="500"):
            jira._search_issues()


# ---------------------------------------------------------------------------
# _fetch_transitions
# ---------------------------------------------------------------------------


class TestFetchTransitions:
    def test_returns_transition_names(
        self, jira: JiraSync, httpx_mock, transitions_response: dict
    ):
        httpx_mock.add_response(json=transitions_response)

        result = jira._fetch_transitions("PROJ-101")
        assert result == ["Start Review", "Done", "Back to To Do"]

    def test_returns_empty_on_error(self, jira: JiraSync, httpx_mock):
        httpx_mock.add_response(status_code=404, text="Not Found")

        result = jira._fetch_transitions("PROJ-999")
        assert result == []


# ---------------------------------------------------------------------------
# TICKET.md output
# ---------------------------------------------------------------------------


class TestWriteTicketMd:
    def test_ticket_md_format(self, jira: JiraSync, tmp_path: Path):
        ticket = Ticket(
            key="PROJ-101",
            summary="Implement auth",
            status="In Progress",
            category="Active Development",
            priority="High",
            issue_type="Story",
            assignee="Alice Smith",
            url="https://jira.example.com/browse/PROJ-101",
            description="Add OAuth2 login flow.",
            epic_key="PROJ-50",
            sprint="Sprint 5",
            customer_name="ERS",
            fix_versions=["1.2.0"],
            components=["Backend"],
            labels=["security"],
            transitions=["Start Review", "Done"],
            comments=[
                Comment(
                    author="Bob Jones",
                    created="2026-03-15T09:00:00.000+0000",
                    body="Working on OAuth provider.",
                ),
            ],
        )

        ticket_dir = tmp_path / "PROJ-101-implement-auth"
        ticket_dir.mkdir()
        (ticket_dir / "orchestrator").mkdir()

        jira._write_ticket_md(ticket, ticket_dir)

        md_path = ticket_dir / "orchestrator" / "TICKET.md"
        assert md_path.exists()
        content = md_path.read_text()

        # Frontmatter.
        assert content.startswith("---\n")
        assert "source: sync" in content
        assert "syncedAt:" in content

        # Title.
        assert "# PROJ-101: Implement auth" in content

        # Metadata table.
        assert "| Status | In Progress |" in content
        assert "| Category | Active Development |" in content
        assert "| Priority | High |" in content
        assert "| Type | Story |" in content
        assert "| Customer Name | ERS |" in content
        assert "| Assignee | Alice Smith |" in content
        assert "| Epic | PROJ-50 |" in content
        assert "| Fix Version | 1.2.0 |" in content
        assert "| Components | Backend |" in content
        assert "| Labels | security |" in content

        # Jira link.
        assert "[View in Jira](https://jira.example.com/browse/PROJ-101)" in content

        # Description.
        assert "## Description" in content
        assert "Add OAuth2 login flow." in content

        # Transitions.
        assert "## Available Transitions" in content
        assert "- Start Review" in content
        assert "- Done" in content

        # Comments.
        assert "## Comments" in content
        assert "### Bob Jones (2026-03-15T09:00:00.000+0000)" in content
        assert "Working on OAuth provider." in content

    def test_omits_empty_sections(self, jira: JiraSync, tmp_path: Path):
        ticket = Ticket(
            key="PROJ-200",
            summary="Minimal ticket",
            status="To Do",
            category="Pre-Development",
            priority="Low",
            issue_type="Task",
            assignee="Unassigned",
            url="https://jira.example.com/browse/PROJ-200",
        )

        ticket_dir = tmp_path / "PROJ-200-minimal-ticket"
        ticket_dir.mkdir()
        (ticket_dir / "orchestrator").mkdir()

        jira._write_ticket_md(ticket, ticket_dir)

        content = (ticket_dir / "orchestrator" / "TICKET.md").read_text()

        assert "## Description" not in content
        assert "## Available Transitions" not in content
        assert "## Comments" not in content
        # Em-dashes for missing fields.
        assert "\u2014" in content

    def test_em_dash_for_missing_fields(self, jira: JiraSync, tmp_path: Path):
        ticket = Ticket(
            key="PROJ-300",
            summary="No versions or components",
            status="To Do",
            category="Pre-Development",
            priority="Medium",
            issue_type="Task",
            assignee="Unassigned",
            url="https://jira.example.com/browse/PROJ-300",
            fix_versions=[],
            components=[],
            labels=[],
        )

        ticket_dir = tmp_path / "PROJ-300-no-extras"
        ticket_dir.mkdir()
        (ticket_dir / "orchestrator").mkdir()

        jira._write_ticket_md(ticket, ticket_dir)

        content = (ticket_dir / "orchestrator" / "TICKET.md").read_text()
        assert "| Fix Version | \u2014 |" in content
        assert "| Components | \u2014 |" in content
        assert "| Labels | \u2014 |" in content
        assert "| Epic | \u2014 |" in content
        # Customer Name row is omitted entirely when the field is absent (non-PS projects).
        assert "Customer Name" not in content


# ---------------------------------------------------------------------------
# Full sync cycle
# ---------------------------------------------------------------------------


class TestFullSync:
    def test_sync_creates_ticket_dirs_and_files(
        self, jira: JiraSync, httpx_mock, search_response: dict, transitions_response: dict,
        tmp_workspace: Path,
    ):
        # Mock search endpoint.
        httpx_mock.add_response(
            url=httpx.URL(
                "https://jira.example.com/rest/api/3/search/jql",
                params={
                    "jql": "assignee = currentUser()",
                    "fields": str(
                        "summary,status,priority,issuetype,assignee,project,parent,"
                        "customfield_10014,customfield_10020,customfield_11704,fixVersions,components,"
                        "labels,comment,description"
                    ),
                    "startAt": "0",
                    "maxResults": "50",
                },
            ),
            json=search_response,
        )

        # Mock /myself so the identity cache write doesn't 404.
        httpx_mock.add_response(
            url="https://jira.example.com/rest/api/3/myself",
            json={"accountId": "user-abc"},
        )

        # Mock transitions for both tickets.
        httpx_mock.add_response(
            url="https://jira.example.com/rest/api/3/issue/PROJ-101/transitions",
            json=transitions_response,
        )
        httpx_mock.add_response(
            url="https://jira.example.com/rest/api/3/issue/PROJ-102/transitions",
            json={"transitions": [
                {"id": "1", "name": "Start Progress", "to": {"name": "In Progress"}},
            ]},
        )

        result = jira.sync(tmp_workspace)

        assert result.source == "jira"
        assert result.tickets_synced == 2
        assert result.errors == []
        assert result.duration_seconds > 0

        # Verify ticket directories were created at root level (flat layout).
        ticket_101_dirs = [
            d for d in tmp_workspace.iterdir()
            if d.is_dir() and d.name.startswith("PROJ-101")
        ]
        assert len(ticket_101_dirs) == 1
        assert (ticket_101_dirs[0] / "orchestrator" / "TICKET.md").exists()

        ticket_102_dirs = [
            d for d in tmp_workspace.iterdir()
            if d.is_dir() and d.name.startswith("PROJ-102")
        ]
        assert len(ticket_102_dirs) == 1
        assert (ticket_102_dirs[0] / "orchestrator" / "TICKET.md").exists()

        # Verify EPIC.md symlinks exist inside orchestrator/ and point to epics/ folder.
        import os
        epic_link_101 = ticket_101_dirs[0] / "orchestrator" / "EPIC.md"
        assert epic_link_101.is_symlink()
        assert not os.path.isabs(os.readlink(epic_link_101))
        assert epic_link_101.resolve().parent.name == "epics"

        epic_link_102 = ticket_102_dirs[0] / "orchestrator" / "EPIC.md"
        assert epic_link_102.is_symlink()

        # Verify epics/ folder contains the epic metadata file.
        epics_dir = tmp_workspace / "epics"
        assert epics_dir.is_dir()
        epic_files = list(epics_dir.iterdir())
        assert len(epic_files) == 1
        assert "PROJ-50" in epic_files[0].name
        assert epic_files[0].name.endswith(".md")

        # Both symlinks should point to the same epic file.
        assert epic_link_101.resolve() == epic_link_102.resolve()

        # Verify TICKET.md content for PROJ-101.
        content_101 = (ticket_101_dirs[0] / "orchestrator" / "TICKET.md").read_text()
        assert "# PROJ-101: Implement user authentication" in content_101
        assert "- Start Review" in content_101

    def test_sync_archives_stale_tickets_in_terminal_status(
        self, jira: JiraSync, httpx_mock, tmp_workspace: Path,
    ):
        # Create a pre-existing ticket directory that will disappear from results.
        stale_dir = tmp_workspace / "STALE-1-old-ticket"
        stale_dir.mkdir()
        (stale_dir / "orchestrator").mkdir()

        # Empty search results — the stale ticket has dropped off the query.
        httpx_mock.add_response(
            json={"startAt": 0, "maxResults": 50, "total": 0, "issues": []},
        )
        # Identity resolution after a successful search.
        httpx_mock.add_response(
            url="https://jira.example.com/rest/api/3/myself",
            json={"accountId": "user-abc"},
        )
        # Follow-up status fetch confirms the ticket is Done, so archiving proceeds.
        httpx_mock.add_response(
            json={"fields": {"status": {"name": "Done"}}},
        )

        result = jira.sync(tmp_workspace)

        assert result.tickets_synced == 0
        assert not stale_dir.exists()
        archive_dir = tmp_workspace / ".archive" / "STALE-1-old-ticket"
        assert archive_dir.exists()

    def test_sync_keeps_stale_tickets_in_non_terminal_status(
        self, jira: JiraSync, httpx_mock, tmp_workspace: Path,
    ):
        # A ticket drops off the query (e.g. reassigned for testing) but is
        # still in a non-terminal status — it should stay in the workspace.
        stale_dir = tmp_workspace / "STALE-2-reassigned-ticket"
        stale_dir.mkdir()
        (stale_dir / "orchestrator").mkdir()

        httpx_mock.add_response(
            json={"startAt": 0, "maxResults": 50, "total": 0, "issues": []},
        )
        httpx_mock.add_response(
            url="https://jira.example.com/rest/api/3/myself",
            json={"accountId": "user-abc"},
        )
        httpx_mock.add_response(
            json={"fields": {"status": {"name": "Analysis Started"}}},
        )

        result = jira.sync(tmp_workspace)

        assert result.tickets_synced == 0
        assert stale_dir.exists()
        assert not (tmp_workspace / ".archive" / "STALE-2-reassigned-ticket").exists()

    def test_sync_keeps_stale_tickets_when_status_fetch_fails(
        self, jira: JiraSync, httpx_mock, tmp_workspace: Path,
    ):
        # If Jira can't tell us the status, we keep the ticket rather than
        # risk archiving something the user is still responsible for.
        stale_dir = tmp_workspace / "STALE-3-unknown-ticket"
        stale_dir.mkdir()
        (stale_dir / "orchestrator").mkdir()

        httpx_mock.add_response(
            json={"startAt": 0, "maxResults": 50, "total": 0, "issues": []},
        )
        httpx_mock.add_response(
            url="https://jira.example.com/rest/api/3/myself",
            json={"accountId": "user-abc"},
        )
        httpx_mock.add_response(status_code=500, text="Internal Server Error")

        result = jira.sync(tmp_workspace)

        assert result.tickets_synced == 0
        assert stale_dir.exists()

    def test_sync_returns_errors_on_auth_failure(
        self, jira: JiraSync, httpx_mock, tmp_workspace: Path,
    ):
        httpx_mock.add_response(status_code=401, text="Unauthorized")

        result = jira.sync(tmp_workspace)

        assert result.tickets_synced == 0
        assert len(result.errors) == 1
        assert "401" in result.errors[0]

    def test_sync_writes_sandbox_settings(
        self, jira_with_sandbox: JiraSync, httpx_mock,
        search_response: dict, transitions_response: dict,
        tmp_workspace: Path,
    ):
        httpx_mock.add_response(json=search_response)
        httpx_mock.add_response(
            url="https://jira.example.com/rest/api/3/myself",
            json={"accountId": "user-abc"},
        )
        httpx_mock.add_response(json=transitions_response)
        httpx_mock.add_response(json={"transitions": []})

        result = jira_with_sandbox.sync(tmp_workspace)

        assert result.tickets_synced == 2
        # Each ticket dir should have .claude/settings.json
        for d in tmp_workspace.iterdir():
            if d.is_dir() and not d.name.startswith(".") and d.name != "epics":
                settings = d / ".claude" / "settings.json"
                assert settings.exists(), f"Missing settings.json in {d.name}"
                import json
                data = json.loads(settings.read_text())
                assert data["sandbox"]["enabled"] is True


# ---------------------------------------------------------------------------
# post_comment
# ---------------------------------------------------------------------------


class TestPostComment:
    def test_posts_adf_body(self, jira: JiraSync, httpx_mock):
        httpx_mock.add_response(
            url="https://jira.example.com/rest/api/3/issue/PROJ-101/comment",
            status_code=201,
            json={"id": "12345"},
        )

        jira.post_comment("PROJ-101", "Implementation complete.\nCI passing.")

        request = httpx_mock.get_request()
        assert request.method == "POST"
        body = json.loads(request.content)
        adf = body["body"]
        assert adf["type"] == "doc"
        assert adf["version"] == 1
        assert len(adf["content"]) == 2
        assert adf["content"][0]["content"][0]["text"] == "Implementation complete."
        assert adf["content"][1]["content"][0]["text"] == "CI passing."

    def test_skips_blank_lines(self, jira: JiraSync, httpx_mock):
        httpx_mock.add_response(status_code=201, json={"id": "1"})

        jira.post_comment("PROJ-101", "Line one\n\n  \nLine two")

        request = httpx_mock.get_request()
        body = json.loads(request.content)
        assert len(body["body"]["content"]) == 2

    def test_empty_body_is_noop(self, jira: JiraSync, httpx_mock):
        jira.post_comment("PROJ-101", "")
        assert len(httpx_mock.get_requests()) == 0

    def test_whitespace_only_body_is_noop(self, jira: JiraSync, httpx_mock):
        jira.post_comment("PROJ-101", "  \n  \n  ")
        assert len(httpx_mock.get_requests()) == 0

    def test_auth_failure_raises(self, jira: JiraSync, httpx_mock):
        httpx_mock.add_response(status_code=401, text="Unauthorized")

        with pytest.raises(AuthError, match="401"):
            jira.post_comment("PROJ-101", "test")

    def test_forbidden_raises(self, jira: JiraSync, httpx_mock):
        httpx_mock.add_response(status_code=403, text="Forbidden")

        with pytest.raises(AuthError, match="403"):
            jira.post_comment("PROJ-101", "test")

    def test_server_error_raises(self, jira: JiraSync, httpx_mock):
        from duct.exceptions import SyncError

        httpx_mock.add_response(status_code=500, text="Internal Server Error")

        with pytest.raises(SyncError, match="500"):
            jira.post_comment("PROJ-101", "test")
