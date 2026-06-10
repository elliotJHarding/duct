"""Setup phases for the wizard.

Each phase configures one concern and shows live data for it — who you
authenticated as, which tickets a JQL matches, which repos a path holds —
so the user never has to guess or verify elsewhere. All probes and writes
go through :mod:`duct.cli.setup_core`.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Input,
    OptionList,
    SelectionList,
    Static,
)
from textual.widgets.option_list import Option
from textual.widgets.selection_list import Selection

from duct.cli import setup_core
from duct.cli.init_cmd import bootstrap_workspace
from duct.cli.setup_wizard.base import Phase
from duct.cli.setup_wizard.path_input import DirectoryPicker, PathInput, contract_home
from duct.credentials import (
    resolve_gh_token_with_source,
    resolve_jira_email,
    resolve_jira_token,
    update_credentials,
)
from duct.global_state import set_workspace_path


def _title(text: str) -> Static:
    return Static(text, classes="phase-title")


def _explain(text: str) -> Static:
    return Static(text, classes="phase-explain")


def _nav(*extra: Button, continue_label: str = "Continue") -> Horizontal:
    return Horizontal(
        Button(continue_label, variant="primary", id="continue"),
        *extra,
        classes="nav-buttons",
    )


# ---------------------------------------------------------------------------
# Welcome
# ---------------------------------------------------------------------------


class WelcomePhase(Phase):
    phase_id = "welcome"
    title = "Welcome"
    rail_label = "Welcome"

    @classmethod
    def is_complete(cls, app) -> bool:
        return app.root is not None

    def compose(self) -> ComposeResult:
        yield _title("Welcome to duct")
        yield Static(
            "duct mirrors your Jira tickets as local folders, tracks PRs and "
            "CI runs against each one, and runs Claude Code sessions per "
            "ticket.\n\n"
            "This wizard configures everything duct needs — workspace, Jira, "
            "GitHub, repo paths — showing you live data at every step, then "
            "gets you started: a first sync, a look inside a ticket, and "
            "adding a repo to one.\n\n"
            "[dim]You can quit at any time (ctrl+q); re-running `duct` picks "
            "up where you left off. Esc goes back a step.[/dim]",
        )
        yield _nav(continue_label="Let's go")

    @on(Button.Pressed, "#continue")
    def _continue(self) -> None:
        self.advance()


# ---------------------------------------------------------------------------
# Workspace
# ---------------------------------------------------------------------------


class WorkspacePhase(Phase):
    phase_id = "workspace"
    title = "Workspace"
    rail_label = "Workspace"

    @classmethod
    def is_complete(cls, app) -> bool:
        return app.root is not None

    def compose(self) -> ComposeResult:
        yield _title("Workspace location")
        yield _explain(
            "The workspace is where duct mirrors your Jira tickets as local "
            "folders, one per ticket. We'll create the directory if needed and "
            "seed a toolkit/ folder holding config.yaml, WORKFLOW.md, agents/, "
            "wiki/, and subagents/.",
        )
        default = self.root or setup_core.default_workspace()
        yield Static(
            "Workspace path [dim](tab completes, like a terminal)[/dim]",
            classes="field-label",
        )
        yield PathInput(value=str(default), id="workspace-path")
        yield Static("", id="workspace-candidates", classes="path-candidates")
        yield Static(
            "Or browse — enter/click a folder to put it in the path box",
            classes="field-label",
        )
        yield DirectoryPicker(Path.home(), id="workspace-tree")
        yield Static("", id="workspace-result", classes="phase-status")
        yield _nav(continue_label="Create workspace")

    @on(PathInput.Candidates, "#workspace-path")
    def _show_candidates(self, event: PathInput.Candidates) -> None:
        names = event.names[:12]
        overflow = f"  [dim]… +{len(event.names) - 12} more[/dim]" if len(event.names) > 12 else ""
        self.query_one("#workspace-candidates", Static).update(
            ("  ".join(names) + overflow) if names else ""
        )

    @on(Input.Changed, "#workspace-path")
    def _clear_candidates(self) -> None:
        self.query_one("#workspace-candidates", Static).update("")

    @on(DirectoryPicker.DirectorySelected, "#workspace-tree")
    def _pick_directory(self, event: DirectoryPicker.DirectorySelected) -> None:
        event.stop()
        path_input = self.query_one("#workspace-path", PathInput)
        path_input.value = contract_home(event.path) + "/"
        path_input.cursor_position = len(path_input.value)
        path_input.focus()

    @on(Button.Pressed, "#continue")
    @on(Input.Submitted, "#workspace-path")
    def _create(self) -> None:
        raw = self.query_one("#workspace-path", Input).value.strip().rstrip("/")
        if not raw:
            return
        self.query_one("#continue", Button).disabled = True
        self._bootstrap(Path(raw).expanduser().resolve())

    @work(thread=True)
    def _bootstrap(self, root: Path) -> None:
        created, _existed = bootstrap_workspace(root)
        set_workspace_path(root)
        self.app.call_from_thread(self._done, root, created)

    def _done(self, root: Path, created: list[str]) -> None:
        detail = f"created {len(created)} files" if created else "already in place"
        self.query_one("#workspace-result", Static).update(
            f"[$success]✓[/] workspace scaffold ready at {root} [dim]({detail})[/dim]"
        )
        self.advance()


# ---------------------------------------------------------------------------
# Jira
# ---------------------------------------------------------------------------


class JiraPhase(Phase):
    phase_id = "jira"
    title = "Jira"
    rail_label = "Jira"

    @classmethod
    def is_complete(cls, app) -> bool:
        return app.cfg is not None and setup_core.jira_configured(app.cfg)

    def compose(self) -> ComposeResult:
        yield _title("Connect Jira")
        yield _explain(
            "duct fetches your tickets via the Jira REST API. The API token "
            "stays on this machine, in your OS keychain, where both the shell "
            "and the background daemon can read it.\n"
            f"Create a token at: {setup_core.JIRA_TOKEN_URL}",
        )
        cfg = self.cfg
        yield Static("Jira domain (e.g. acme.atlassian.net)", classes="field-label")
        yield Input(value=cfg.jira_domain if cfg else "", id="jira-domain")
        yield Static("Jira email", classes="field-label")
        yield Input(
            value=resolve_jira_email() or setup_core.git_email_default() or "",
            id="jira-email",
        )
        has_token = bool(resolve_jira_token())
        token_label = (
            "API token (saved token found — leave blank to reuse it)"
            if has_token else "API token"
        )
        yield Static(token_label, classes="field-label")
        yield Input(password=True, id="jira-token")
        yield Static("", id="jira-result", classes="phase-status")
        yield _nav(continue_label="Verify & continue")

    def on_mount(self) -> None:
        cfg = self.cfg
        if cfg is not None and setup_core.jira_configured(cfg):
            self._verify_existing(cfg.jira_domain, resolve_jira_email(), resolve_jira_token())

    @work(thread=True)
    def _verify_existing(self, domain: str, email: str, token: str) -> None:
        ok, detail = setup_core.jira_user(domain, email, token)
        if ok:
            self.app.call_from_thread(
                self._show_status,
                f"[$success]✓[/] already configured — authenticated as {detail}. "
                "Press Continue to keep these details.",
            )

    @on(Button.Pressed, "#continue")
    @on(Input.Submitted)
    def _verify(self) -> None:
        domain = self.query_one("#jira-domain", Input).value.strip().lower()
        email = self.query_one("#jira-email", Input).value.strip()
        token = self.query_one("#jira-token", Input).value.strip() or resolve_jira_token()
        if not domain or not email or not token:
            self._show_status("[$error]✗ domain, email, and an API token are all required[/]")
            return
        self.query_one("#continue", Button).disabled = True
        self._show_status("[dim]verifying against Jira…[/dim]")
        self._probe(domain, email, token)

    @work(thread=True, exclusive=True, group="jira-probe")
    def _probe(self, domain: str, email: str, token: str) -> None:
        ok, detail = setup_core.jira_user(domain, email, token)
        self.app.call_from_thread(self._probed, ok, detail, domain, email, token)

    def _probed(self, ok: bool, detail: str, domain: str, email: str, token: str) -> None:
        self.query_one("#continue", Button).disabled = False
        if not ok:
            self._show_status(f"[$error]✗ Jira auth failed — {detail}[/]")
            return
        assert self.root is not None
        update_credentials(jira_email=email, jira_token=token)
        setup_core.update_config(self.root, jira_domain=domain)
        self._show_status(f"[$success]✓[/] authenticated as {detail}")
        self.advance()

    def _show_status(self, markup: str) -> None:
        self.query_one("#jira-result", Static).update(markup)


# ---------------------------------------------------------------------------
# JQL with live ticket preview
# ---------------------------------------------------------------------------


class JqlPhase(Phase):
    phase_id = "jql"
    title = "Ticket filter"
    rail_label = "Ticket filter (JQL)"

    @classmethod
    def is_complete(cls, app) -> bool:
        return JiraPhase.is_complete(app)

    def compose(self) -> ComposeResult:
        yield _title("Which tickets should duct track?")
        yield _explain(
            "JQL controls which tickets duct syncs into your workspace. The "
            "default picks every ticket assigned to you that isn't Done. "
            "Edit it and the preview below updates live — what you see here "
            "is exactly what sync will mirror as folders.",
        )
        yield Input(value=self.cfg.jira_jql if self.cfg else "", id="jql-input")
        yield Static("", id="jql-count", classes="phase-status")
        table: DataTable = DataTable(id="jql-preview", cursor_type="row")
        table.add_columns("Key", "Summary", "Status", "Updated")
        yield table
        yield _nav()

    def on_mount(self) -> None:
        self._schedule_preview()

    @on(Input.Changed, "#jql-input")
    def _changed(self) -> None:
        self._schedule_preview()

    def _schedule_preview(self) -> None:
        if hasattr(self, "_debounce"):
            self._debounce.stop()
        self._debounce = self.set_timer(0.5, self._kick_preview)

    def _kick_preview(self) -> None:
        jql = self.query_one("#jql-input", Input).value.strip()
        cfg = self.cfg
        if not jql or cfg is None or not cfg.jira_domain:
            return
        self.query_one("#jql-count", Static).update("[dim]querying Jira…[/dim]")
        self._preview(cfg.jira_domain, resolve_jira_email(), resolve_jira_token(), jql)

    @work(thread=True, exclusive=True, group="jql-preview")
    def _preview(self, domain: str, email: str, token: str, jql: str) -> None:
        count = setup_core.jql_count(domain, email, token, jql)
        issues, error = setup_core.jql_preview(domain, email, token, jql)
        self.app.call_from_thread(self._show_preview, jql, count, issues, error)

    def _show_preview(self, jql, count, issues, error) -> None:
        if jql != self.query_one("#jql-input", Input).value.strip():
            return  # stale result — a newer edit is already in flight
        table = self.query_one("#jql-preview", DataTable)
        table.clear()
        if issues is None:
            self.query_one("#jql-count", Static).update(f"[$error]✗ {error}[/]")
            return
        shown = len(issues)
        total = count if count is not None else shown
        suffix = f" (showing first {shown})" if total > shown else ""
        self.query_one("#jql-count", Static).update(
            f"[$success]✓[/] {total} matching tickets{suffix}"
        )
        for issue in issues:
            # Text() keeps bracketed summaries ("[Backend] Fix …") from being
            # parsed as Rich markup.
            table.add_row(
                issue.key, Text(issue.summary[:100]), issue.status, issue.updated,
            )

    @on(Button.Pressed, "#continue")
    @on(Input.Submitted, "#jql-input")
    def _continue(self) -> None:
        jql = self.query_one("#jql-input", Input).value.strip()
        if not jql:
            return
        assert self.root is not None and self.cfg is not None
        if jql != self.cfg.jira_jql:
            setup_core.update_config(self.root, jira_jql=jql)
        self.advance()


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------


class GithubPhase(Phase):
    phase_id = "github"
    title = "GitHub"
    rail_label = "GitHub"
    skippable = True

    @classmethod
    def is_complete(cls, app) -> bool:
        return setup_core.github_token_available()

    def compose(self) -> ComposeResult:
        yield _title("Connect GitHub")
        yield _explain(
            "duct tracks PRs and CI runs against each ticket by polling the "
            "GitHub API, which needs a token. GitHub sync is optional — "
            "skip with ctrl+s and duct will track Jira only.",
        )
        yield Static("", id="github-status", classes="phase-status")
        with Vertical(id="github-token-form"):
            with Horizontal(classes="nav-buttons"):
                yield Button("Run `gh auth login`", id="gh-login")
                yield Button("Check again", id="gh-recheck")
            yield Static(
                "Or paste a personal access token (classic or fine-grained; "
                f"needs `repo` and `read:org`) — create one at "
                f"{setup_core.GITHUB_TOKEN_URL}",
                classes="field-label",
            )
            yield Input(
                password=True, placeholder="ghp_…  (enter to verify)",
                id="github-pat",
            )
        yield SelectionList(id="github-orgs")
        yield Static(
            "Pick the orgs duct should watch for PRs.", id="github-orgs-hint",
            classes="phase-explain",
        )
        yield _nav()

    def on_mount(self) -> None:
        self.query_one("#github-orgs", SelectionList).display = False
        self.query_one("#github-orgs-hint", Static).display = False
        self._manual_token = ""
        self._refresh_auth()

    @on(Button.Pressed, "#gh-recheck")
    def _refresh_auth(self) -> None:
        """Detect the current token state and say it out loud."""
        status = self.query_one("#github-status", Static)
        form = self.query_one("#github-token-form", Vertical)
        token, source = resolve_gh_token_with_source()
        if token:
            form.display = False
            status.update(f"[dim]found a token in your {source} — verifying…[/dim]")
            self._probe(token, source)
            return
        gh_installed = bool(shutil.which("gh"))
        self.query_one("#gh-login", Button).display = gh_installed
        if gh_installed:
            status.update(
                "[$warning]●[/] No GitHub token found — the gh CLI is "
                "installed but not logged in. Log in below, or paste a token."
            )
        else:
            status.update(
                "[$warning]●[/] No GitHub token found, and the gh CLI isn't "
                "installed. Easiest fix: install it and log in from another "
                "terminal ([bold]brew install gh && gh auth login[/bold]), "
                "then press Check again. Or paste a token below."
            )
        form.display = True

    @on(Input.Submitted, "#github-pat")
    def _pat_entered(self) -> None:
        token = self.query_one("#github-pat", Input).value.strip()
        if token:
            self._manual_token = token
            self.query_one("#github-status", Static).update(
                "[dim]checking pasted token…[/dim]"
            )
            self._probe(token, "pasted")

    @on(Button.Pressed, "#gh-login")
    def _gh_login(self) -> None:
        with self.app.suspend():
            subprocess.run(["gh", "auth", "login"], check=False)
        self._refresh_auth()

    @work(thread=True, exclusive=True, group="github-probe")
    def _probe(self, token: str, source: str) -> None:
        ok, login, orgs = setup_core.github_user(token)
        counts: dict[str, int | None] = {}
        if ok:
            counts = {org: setup_core.org_repo_count(token, org) for org in orgs}
        self.app.call_from_thread(self._probed, token, source, ok, login, orgs, counts)

    def _probed(self, token, source, ok, login, orgs, counts) -> None:
        status = self.query_one("#github-status", Static)
        if not ok:
            status.update(
                f"[$error]✗ GitHub rejected the {source} token — {login}.[/] "
                "Log in again or paste a different token."
            )
            self.query_one("#gh-login", Button).display = bool(shutil.which("gh"))
            self.query_one("#github-token-form", Vertical).display = True
            return
        self._token = token
        status.update(
            f"[$success]✓[/] authenticated as {login} "
            f"[dim]— using your {source} token, nothing else to do here[/dim]"
        )
        self.query_one("#github-token-form", Vertical).display = False
        selection = self.query_one("#github-orgs", SelectionList)
        selection.clear_options()
        if not orgs:
            self.query_one("#github-orgs-hint", Static).update(
                "[dim]No org memberships visible to this token.[/dim]"
            )
            self.query_one("#github-orgs-hint", Static).display = True
            return
        current = set(self.cfg.github_orgs) if self.cfg else set()
        for org in orgs:
            count = counts.get(org)
            label = f"{org} [dim]({count} repos)[/dim]" if count is not None else org
            selection.add_option(Selection(label, org, initial_state=org in current))
        selection.display = True
        self.query_one("#github-orgs-hint", Static).display = True

    @on(Button.Pressed, "#continue")
    def _continue(self) -> None:
        assert self.root is not None
        selection = self.query_one("#github-orgs", SelectionList)
        if selection.display:
            picked = tuple(selection.selected)
            if picked:
                setup_core.update_config(self.root, github_orgs=picked)
        # Persist a manually-entered PAT; tokens borrowed from gh CLI/env
        # stay where they are (gh manages its own keychain entry).
        if self._manual_token:
            update_credentials(gh_token=self._manual_token)
        self.advance()


# ---------------------------------------------------------------------------
# Repo paths with live discovery
# ---------------------------------------------------------------------------


class RepoPathsPhase(Phase):
    phase_id = "repo-paths"
    title = "Repo paths"
    rail_label = "Repo paths"

    @classmethod
    def is_complete(cls, app) -> bool:
        return app.cfg is not None and any(p.is_dir() for p in app.cfg.repo_paths)

    def compose(self) -> ComposeResult:
        yield _title("Where do you keep your repos?")
        yield _explain(
            "duct scans these directories to find local clones of repos "
            "referenced by tickets, so it can create per-ticket worktrees. "
            "Each path below shows the git repos duct actually finds there — "
            "untick any path you don't use.",
        )
        yield Vertical(id="repo-rows")
        yield Static("Add another path", classes="field-label")
        yield Input(placeholder="~/code", id="repo-add")
        yield _nav()

    def on_mount(self) -> None:
        for path in (self.cfg.repo_paths if self.cfg else []):
            self._add_row(path)

    def _add_row(self, path: Path) -> None:
        exists = path.is_dir()
        row = Horizontal(
            Checkbox(str(path), value=exists, id=None),
            Static(
                "[dim]scanning…[/dim]" if exists else "[$warning]directory not found[/]",
                classes="repo-detail",
            ),
            classes="repo-row",
        )
        row.styles.height = "auto"
        self.query_one("#repo-rows", Vertical).mount(row)
        if exists:
            self._scan(path, row)

    @work(thread=True)
    def _scan(self, path: Path, row: Horizontal) -> None:
        repos = setup_core.repos_under(path)
        self.app.call_from_thread(self._scanned, row, repos)

    def _scanned(self, row: Horizontal, repos: list[str]) -> None:
        if repos:
            sample = ", ".join(repos[:6]) + ("…" if len(repos) > 6 else "")
            detail = f"[dim]{len(repos)} repos: {sample}[/dim]"
        else:
            detail = "[dim]no git repos found[/dim]"
        row.query_one(".repo-detail", Static).update(detail)

    @on(Input.Submitted, "#repo-add")
    def _add(self) -> None:
        field = self.query_one("#repo-add", Input)
        raw = field.value.strip()
        if not raw:
            return
        field.value = ""
        self._add_row(Path(raw).expanduser().resolve())

    @on(Button.Pressed, "#continue")
    def _continue(self) -> None:
        assert self.root is not None and self.cfg is not None
        kept = [
            Path(box.label.plain).expanduser()
            for box in self.query(Checkbox)
            if box.value
        ]
        if [str(p) for p in kept] != [str(p) for p in self.cfg.repo_paths]:
            setup_core.update_config(self.root, repo_paths=kept)
        self.advance()


# ---------------------------------------------------------------------------
# External tools
# ---------------------------------------------------------------------------


class ToolsPhase(Phase):
    phase_id = "tools"
    title = "External tools"
    rail_label = "External tools"

    @classmethod
    def is_complete(cls, app) -> bool:
        return all(t.present for t in setup_core.tool_statuses() if t.required)

    def compose(self) -> ComposeResult:
        yield _title("External tools")
        yield _explain(
            "duct shells out to a few CLIs. `claude` and `git` are required; "
            "`gh` and `mmdc` are optional polish. This check is read-only — "
            "nothing is installed for you.",
        )
        lines = []
        for tool in setup_core.tool_statuses():
            if tool.present:
                lines.append(f"[$success]✓[/] {tool.name}")
            elif tool.required:
                lines.append(f"[$error]✗ {tool.name}[/] — {tool.hint}")
            else:
                lines.append(f"[$text-muted]· {tool.name} — {tool.hint}[/]")
        yield Static("\n".join(lines), classes="live-panel")
        yield _nav()

    @on(Button.Pressed, "#continue")
    def _continue(self) -> None:
        self.advance()


# ---------------------------------------------------------------------------
# Shell completion
# ---------------------------------------------------------------------------


class WikiPhase(Phase):
    phase_id = "wiki"
    title = "Workspace wiki"
    rail_label = "Wiki"
    skippable = True

    @classmethod
    def is_complete(cls, app) -> bool:
        return app.root is not None  # defaults are valid; phase is a choice

    def compose(self) -> ComposeResult:
        yield _title("Workspace wiki")
        yield _explain(
            "duct can keep a curated knowledge base in toolkit/wiki/ — "
            "lessons from corrections, project conventions, domain notes, "
            "and environment quirks — written and consulted by three Claude "
            "Code subagents that sessions invoke automatically. Off by "
            "default; sessions run leaner without it. Change it any time by "
            "re-running duct setup.",
        )
        enabled = bool(self.cfg and self.cfg.wiki.enabled)
        yield Checkbox("Enable the workspace wiki", value=enabled, id="wiki-enabled")
        yield _nav()

    @on(Button.Pressed, "#continue")
    def _continue(self) -> None:
        assert self.root is not None
        enabled = self.query_one("#wiki-enabled", Checkbox).value
        setup_core.set_wiki(self.root, enabled)
        if not enabled and setup_core.toolkit_claude_mentions_wiki(self.root):
            self.notify(
                "toolkit/CLAUDE.md still mentions the wiki — duct never edits "
                "user files, so remove that section yourself.",
                severity="warning",
            )
        self.advance()


class CompletionPhase(Phase):
    phase_id = "completion"
    title = "Shell completion"
    rail_label = "Shell completion"
    skippable = True

    @classmethod
    def is_complete(cls, app) -> bool:
        status = setup_core.shell_completion_status()
        return status is None or status.enabled

    def compose(self) -> ComposeResult:
        yield _title("Shell completion")
        yield _explain(
            "duct ships tab-completion for ticket keys, repo names, session "
            "IDs, and agent names. Enabling it appends one line to your "
            "shell rc.",
        )
        status = setup_core.shell_completion_status()
        if status is None:
            yield Static("[dim]Unknown shell — skipping completion setup.[/dim]")
            yield _nav()
            return
        if status.enabled:
            yield Static(
                f"[$success]✓[/] completion already enabled in {status.rc_path.name}",
                classes="phase-status",
            )
            yield _nav()
            return
        yield Static(
            f"Would append to [bold]{status.rc_path}[/bold]:\n"
            f"[dim]{status.activation}[/dim]",
            classes="live-panel",
        )
        yield _nav(
            Button("Not now", id="decline"),
            continue_label="Enable completion",
        )

    @on(Button.Pressed, "#continue")
    def _continue(self) -> None:
        status = setup_core.shell_completion_status()
        if status is not None and not status.enabled:
            setup_core.enable_shell_completion(status)
            self.notify(f"Completion added to {status.rc_path.name}")
        self.advance()

    @on(Button.Pressed, "#decline")
    def _decline(self) -> None:
        self.advance()


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


class NotificationsPhase(Phase):
    phase_id = "notifications"
    title = "Notifications"
    rail_label = "Notifications"
    skippable = True

    _KINDS = (
        ("done", "Session finished"),
        ("waiting", "Session waiting for input"),
        ("pending-action", "Action proposed and awaiting review"),
        ("orchestrator", "Orchestrator run finished"),
    )

    @classmethod
    def is_complete(cls, app) -> bool:
        return app.root is not None  # defaults are valid; phase is a choice

    def compose(self) -> ComposeResult:
        yield _title("macOS notifications")
        yield _explain(
            "The background daemon can fire macOS notifications when "
            "sessions finish, need input, or propose actions. Off by "
            "default — enable it here if you want them.",
        )
        cfg = self.cfg
        enabled = bool(cfg and cfg.notifications.enabled)
        current = set(cfg.notifications.event_kinds) if cfg else set()
        yield Checkbox("Enable notifications", value=enabled, id="notif-enabled")
        yield SelectionList(
            *[
                Selection(label, kind, initial_state=kind in current)
                for kind, label in self._KINDS
            ],
            id="notif-kinds",
        )
        yield _nav()

    @on(Button.Pressed, "#continue")
    def _continue(self) -> None:
        assert self.root is not None
        enabled = self.query_one("#notif-enabled", Checkbox).value
        kinds = tuple(self.query_one("#notif-kinds", SelectionList).selected)
        setup_core.set_notifications(self.root, enabled, kinds or tuple(k for k, _ in self._KINDS))
        self.advance()


# ---------------------------------------------------------------------------
# Daemon
# ---------------------------------------------------------------------------


class DaemonPhase(Phase):
    phase_id = "daemon"
    title = "Background daemon"
    rail_label = "Daemon"
    skippable = True

    @classmethod
    def is_complete(cls, app) -> bool:
        return setup_core.daemon_installed()

    def compose(self) -> ComposeResult:
        yield _title("Background daemon")
        yield _explain(
            "The daemon keeps your data fresh, fires notifications, and runs "
            "scheduled orchestrator passes — all without the TUI open. It "
            "installs as a launchd agent that starts at login.",
        )
        if setup_core.daemon_installed():
            yield Static("[$success]✓[/] daemon already installed", classes="phase-status")
            yield _nav()
            return
        yield Static("", id="daemon-result", classes="phase-status")
        yield _nav(
            Button("Not now", id="decline"),
            continue_label="Install daemon",
        )

    @on(Button.Pressed, "#continue")
    def _continue(self) -> None:
        if setup_core.daemon_installed():
            self.advance()
            return
        self.query_one("#continue", Button).disabled = True
        self._install()

    @work(thread=True)
    def _install(self) -> None:
        assert self.root is not None
        try:
            setup_core.install_daemon(self.root)
        except Exception as exc:  # noqa: BLE001 — report and let the user continue
            self.app.call_from_thread(self._failed, str(exc))
            return
        self.app.call_from_thread(self.advance)

    def _failed(self, detail: str) -> None:
        self.query_one("#continue", Button).disabled = False
        self.query_one("#daemon-result", Static).update(
            f"[$error]✗ daemon install failed — {detail}[/]\n"
            "[dim]Continue and install later with `duct daemon install`.[/dim]"
        )

    @on(Button.Pressed, "#decline")
    def _decline(self) -> None:
        self.advance()


# ---------------------------------------------------------------------------
# First sync (mandatory) + tutorial offer
# ---------------------------------------------------------------------------


def _workspace_tree(root: Path) -> str:
    """Plain-text listing of the whole workspace — every ticket, every file.

    toolkit/ and .duct/ stay collapsed (they're duct's own machinery); the
    interesting part is ticket folders appearing and filling up as sources
    finish.
    """
    toolkit_note = "config.yaml · WORKFLOW.md · agents/"
    if (root / "toolkit" / "wiki").is_dir():
        toolkit_note += " · wiki/"
    collapsed = {
        "toolkit": toolkit_note,
        ".duct": "runtime state — sync timestamps, caches",
    }
    lines = [f"{root.name}/"]

    def walk(directory: Path, indent: str, depth: int) -> None:
        try:
            entries = sorted(
                directory.iterdir(),
                key=lambda p: (p.is_file(), p.name.lower()),
            )
        except OSError:
            return
        if directory == root:
            entries = [
                e for e in entries
                if not e.name.startswith(".") or e.name in (".duct", ".archive")
            ]
        else:
            entries = [e for e in entries if not e.name.startswith(".")]
        for position, entry in enumerate(entries):
            last = position == len(entries) - 1
            branch, extend = ("└── ", "    ") if last else ("├── ", "│   ")
            if entry.is_dir():
                note = collapsed.get(entry.name) if directory == root else None
                suffix = f"   {note}" if note else ""
                lines.append(f"{indent}{branch}{entry.name}/{suffix}")
                if note is None and depth < 3:
                    walk(entry, indent + extend, depth + 1)
            else:
                lines.append(f"{indent}{branch}{entry.name}")

    walk(root, "", 1)
    return "\n".join(lines)


class SyncPhase(Phase):
    phase_id = "sync"
    title = "Sync"
    rail_label = "Sync"
    group = "Get started"

    # Stagger sources a touch so the tree visibly fills in step with the
    # table — first-run sync is the best demo of what sync does.
    _DEMO_DELAY_SECONDS = 0.8

    @classmethod
    def is_complete(cls, app) -> bool:
        return app.root is not None and setup_core.first_sync_done(app.root)

    def compose(self) -> ComposeResult:
        yield _title("Sync — your tickets become folders")
        with Horizontal(id="sync-body"):
            with Vertical(id="sync-left"):
                yield _explain(
                    "Each source writes files into your workspace — watch "
                    "them land in the tree as it runs.",
                )
                table: DataTable = DataTable(id="sync-table", cursor_type="none")
                table.add_column("Source")
                table.add_column("Status", key="status")
                yield table
                yield Static("", id="sync-summary", classes="phase-status")
                archive_note = ""
                if self.cfg is not None:
                    terminal = ", ".join(self.cfg.status.terminal_statuses)
                    archive_note = (
                        f"[dim]When a ticket reaches {terminal}, sync moves "
                        "its folder to .archive/.[/dim]"
                    )
                yield Static(archive_note, classes="phase-explain")
                yield _nav()
            with VerticalScroll(id="sync-tree-panel"):
                yield Static("", id="sync-tree", markup=False)

    def on_mount(self) -> None:
        self.query_one("#continue", Button).display = False
        self._refresh_tree()
        # Sync gives the add-repo chapter's slow data a head start (covers
        # resumes that never pass through the GitHub phase).
        self.wizard.warm_repo_cache()
        self._run_sync()

    @work(thread=True)
    def _run_sync(self) -> None:
        import time

        from duct.cli.sync_cmd import _refresh_repo_completion_cache
        from duct.sync.base import SyncCoordinator

        assert self.root is not None and self.cfg is not None
        root, cfg = self.root, self.cfg
        sources, skipped = setup_core.build_sync_sources(cfg)

        # Table cells render via Rich markup, which doesn't know Textual's
        # $theme variables — concrete colors only here.
        rows = [(s.name, "[dim]queued[/dim]") for s in sources]
        rows += [(name, f"[yellow]skipped — {reason}[/]") for name, reason in skipped]
        self.app.call_from_thread(self._seed_table, rows)

        coordinator = SyncCoordinator(root, setup_core.sync_intervals(cfg))

        def on_start(name: str) -> None:
            self.app.call_from_thread(self._mark, name, "[bold yellow]● syncing…[/]")

        def on_result(result) -> None:
            if result.errors:
                detail = f"[red]✗ {result.errors[0][:60]}[/]"
            else:
                detail = (
                    f"[green]✓[/] {result.tickets_synced} tickets "
                    f"in {result.duration_seconds:.1f}s"
                )
            self.app.call_from_thread(self._mark, result.source, detail)
            self.app.call_from_thread(self._refresh_tree)
            time.sleep(self._DEMO_DELAY_SECONDS)

        results = coordinator.run(sources, force=True, on_start=on_start, on_result=on_result)
        _refresh_repo_completion_cache(root, cfg)
        total = sum(r.tickets_synced for r in results)
        self.app.call_from_thread(self._finished, total)

    def _seed_table(self, rows: list[tuple[str, str]]) -> None:
        table = self.query_one("#sync-table", DataTable)
        for name, status in rows:
            table.add_row(name, status, key=name)

    def _mark(self, source: str, status: str) -> None:
        self.query_one("#sync-table", DataTable).update_cell(
            source, "status", status, update_width=True,
        )

    def _refresh_tree(self) -> None:
        if self.root is not None:
            self.query_one("#sync-tree", Static).update(_workspace_tree(self.root))

    def _finished(self, total_tickets: int) -> None:
        summary = self.query_one("#sync-summary", Static)
        if total_tickets:
            summary.update(
                f"[$success]✓[/] sync complete — {total_tickets} tickets in your workspace"
            )
        else:
            summary.update(
                "[$warning]Sync finished but matched no tickets.[/] "
                "Check the JQL phase if that's unexpected."
            )
        button = self.query_one("#continue", Button)
        if self.wizard.jump_mode:
            button.label = "Done"
        button.display = True
        button.focus()

    @on(Button.Pressed, "#continue")
    def _continue(self) -> None:
        self.advance()


# ---------------------------------------------------------------------------
# Jump menu — shown instead of the linear walk when duct is already set up.
# ---------------------------------------------------------------------------


class JumpMenuPhase(Phase):
    phase_id = "menu"
    title = "duct setup"
    rail_label = "Menu"

    def compose(self) -> ComposeResult:
        yield _title("duct is set up — what do you want to revisit?")
        menu = OptionList(id="jump-menu")
        for phase_cls in SETUP_PHASES:
            if phase_cls is WelcomePhase:
                continue
            marker = "[$success]✓[/]" if phase_cls.is_complete(self.wizard) else "[$text-muted]○[/]"
            menu.add_option(Option(f"{marker} {phase_cls.title}", id=phase_cls.phase_id))
        menu.add_option(None)
        from duct.cli.setup_wizard.app import tutorial_completed

        tour_marker = "[$success]✓[/]" if tutorial_completed() else "[$text-muted]○[/]"
        menu.add_option(Option(f"{tour_marker} Get started (sync + tour)", id="tour"))
        menu.add_option(Option("  Exit", id="exit"))
        yield menu

    def on_mount(self) -> None:
        self.query_one("#jump-menu", OptionList).focus()

    @on(OptionList.OptionSelected, "#jump-menu")
    def _selected(self, event: OptionList.OptionSelected) -> None:
        choice = event.option.id
        if choice == "exit" or choice is None:
            self.app.exit(0)
        elif choice == "tour":
            self.wizard.start_tutorial()
        else:
            self.wizard.jump_to(choice)


def _setup_phases() -> list[type[Phase]]:
    phases: list[type[Phase]] = [
        WelcomePhase,
        WorkspacePhase,
        JiraPhase,
        JqlPhase,
        GithubPhase,
        RepoPathsPhase,
        ToolsPhase,
        WikiPhase,
        CompletionPhase,
    ]
    if setup_core.daemon_supported():
        phases += [NotificationsPhase, DaemonPhase]
    return phases


SETUP_PHASES: list[type[Phase]] = _setup_phases()
