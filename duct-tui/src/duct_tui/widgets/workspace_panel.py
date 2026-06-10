"""WorkspacePanel -- per-ticket repo list + inline add-repo composer."""

from __future__ import annotations

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Button, Input, Label, Static

from duct.cli.workspace_cmd import RepoCandidate
from duct.models import RepoStatus
from duct_tui.icons import UNICODE
from duct_tui.widgets.fuzzy_combobox import ComboOption, FuzzyCombobox
from duct_tui.widgets.workspace_render import render_repo_columns


class _RepoGrid(VerticalScroll):
    """Repo cards flowed into responsive columns, scrollable and focusable."""

    can_focus = True

    BINDINGS = [
        Binding("j", "scroll_down", "Down", show=False),
        Binding("k", "scroll_up", "Up", show=False),
        Binding("r", "focus_form", "Add repo", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield Static(id="repo-cards")

    def update_repos(self, repos: list[RepoStatus]) -> None:
        cards = self.query_one("#repo-cards", Static)
        if not repos:
            hint = Text()
            hint.append("No repos yet", style="dim italic")
            hint.append("\n")
            hint.append("Press ", style="dim")
            hint.append("r", style="bold")
            hint.append(" to add one below", style="dim")
            cards.update(hint)
            return
        icons = getattr(self.app, "icons", UNICODE)
        cards.update(render_repo_columns(repos, icons))

    def action_focus_form(self) -> None:
        self.post_message(WorkspacePanel.FocusAddRepoForm())


class WorkspacePanel(Widget):
    """Repo list above, inline add-repo form below."""

    BINDINGS = [
        Binding("r", "focus_form", "Add repo", show=False),
    ]

    class AddRepoRequested(Message):
        def __init__(
            self,
            repo_name: str,
            base_branch: str,
            feature_branch: str,
            clone_from: str | None = None,
        ) -> None:
            super().__init__()
            self.repo_name = repo_name
            self.base_branch = base_branch
            self.feature_branch = feature_branch
            self.clone_from = clone_from

    class FocusAddRepoForm(Message):
        """Internal: focus the repo Select in the inline form."""

    def __init__(self, ticket_key: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._ticket_key = ticket_key
        self._seeded = False
        self._candidates: dict[str, RepoCandidate] = {}
        self._selected: RepoCandidate | None = None
        self.border_title = "Workspace"

    def compose(self) -> ComposeResult:
        yield _RepoGrid(id="repo-list")
        with Vertical(id="add-repo-form"):
            yield Label("Add repo", id="add-repo-heading")
            yield Label("Repository", classes="field-label")
            yield FuzzyCombobox(
                placeholder="Repo (type to filter)",
                id="repo-combo",
            )
            yield Label("Base branch", classes="field-label")
            yield FuzzyCombobox(
                placeholder="Branch to fork from",
                id="base-branch-combo",
            )
            yield Label("Feature branch", classes="field-label")
            yield Input(placeholder="Feature branch name", id="feature-branch")
            yield Button("Add worktree", variant="primary", id="add-btn")
            yield Static("", id="add-repo-status")

    def on_mount(self) -> None:
        self._seed_form()

    # -- Data in --

    def update_repos(self, repos: list[RepoStatus]) -> None:
        self.query_one("#repo-list", _RepoGrid).update_repos(repos)

    # -- Form seeding / reactions --

    @work(thread=True, exclusive=True)
    def _seed_form(self) -> None:
        if self._seeded:
            return
        try:
            candidates = self.app.data.get_repo_candidates()
            default_branch = self.app.data.suggest_feature_branch(self._ticket_key)
        except Exception as exc:
            self.app.call_from_thread(self._set_status, f"Error: {exc}", "error")
            return
        self.app.call_from_thread(self._apply_seed, candidates, default_branch)

    def _apply_seed(
        self, candidates: list[RepoCandidate], default_branch: str,
    ) -> None:
        self._seeded = True
        self._candidates = {c.name: c for c in candidates}
        combo = self.query_one("#repo-combo", FuzzyCombobox)
        if candidates:
            combo.set_options([
                ComboOption(
                    value=c.name,
                    label=c.name,
                    secondary=self._describe_candidate(c),
                )
                for c in candidates
            ])
        else:
            self._set_status(
                "No repos found. Configure repoPaths or githubOrgs in config.yaml.",
                "warn",
            )
        feature_input = self.query_one("#feature-branch", Input)
        if not feature_input.value:
            feature_input.value = default_branch

    @staticmethod
    def _describe_candidate(c: RepoCandidate) -> str:
        if c.is_remote_only and c.slug:
            return f"↓ {c.slug}"
        if c.slug:
            return c.slug
        return "local"

    def on_fuzzy_combobox_selected(
        self, event: FuzzyCombobox.Selected,
    ) -> None:
        event.stop()
        combo_id = event.combobox.id
        if combo_id == "repo-combo":
            candidate = self._candidates.get(event.value)
            if candidate is None:
                return
            self._selected = candidate
            self._populate_branches(candidate)
        elif combo_id == "base-branch-combo":
            self.query_one("#feature-branch", Input).focus()

    def on_fuzzy_combobox_cancelled(
        self, event: FuzzyCombobox.Cancelled,
    ) -> None:
        event.stop()
        self.query_one("#repo-list", _RepoGrid).focus()

    @work(thread=True, exclusive=True, group="workspace-branches")
    def _populate_branches(self, candidate: RepoCandidate) -> None:
        self.app.call_from_thread(
            self._set_status, "Fetching branches from origin…", "info",
        )
        try:
            branches = self.app.data.list_repo_branches(
                candidate.name, slug=candidate.slug,
            )
        except Exception as exc:
            self.app.call_from_thread(self._set_status, f"Error: {exc}", "error")
            return
        self.app.call_from_thread(self._apply_branches, candidate, branches)

    def _apply_branches(
        self, candidate: RepoCandidate, branches: list[str],
    ) -> None:
        base_combo = self.query_one("#base-branch-combo", FuzzyCombobox)
        if not branches:
            base_combo.set_options([])
            hint = (
                "No branches from gh api (not authenticated?). "
                "Pick 'main' or type a name."
                if candidate.is_remote_only
                else "No branches found in repo."
            )
            self._set_status(hint, "warn")
            base_combo.focus_input()
            return
        base_combo.set_options([
            ComboOption(value=b, label=b) for b in branches
        ])
        preferred = candidate.default_branch or next(
            (p for p in ("main", "master", "develop") if p in branches),
            None,
        )
        if preferred and preferred in branches:
            base_combo.set_value(preferred)
        self._set_status("", "info")
        base_combo.focus_input()

    # -- Submit --

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "add-btn":
            return
        event.stop()
        self._submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.control.id == "feature-branch":
            event.stop()
            self._submit()

    def _submit(self) -> None:
        repo_combo = self.query_one("#repo-combo", FuzzyCombobox)
        base_combo = self.query_one("#base-branch-combo", FuzzyCombobox)
        repo_name = repo_combo.value.strip()
        base = base_combo.value.strip()
        feature = self.query_one("#feature-branch", Input).value.strip()
        candidate = self._candidates.get(repo_name) or self._selected
        if not candidate or candidate.name != repo_name:
            self._set_status("Pick a repo first.", "warn")
            return
        if not base:
            self._set_status("Pick a base branch.", "warn")
            return
        if not feature:
            self._set_status("Feature branch name required.", "warn")
            return
        if candidate.is_remote_only:
            self._set_status(
                f"Cloning {candidate.slug or candidate.name}…", "info",
            )
        else:
            self._set_status("Creating worktree…", "info")
        self.post_message(self.AddRepoRequested(
            repo_name=candidate.name,
            base_branch=base,
            feature_branch=feature,
            clone_from=candidate.slug if candidate.is_remote_only else None,
        ))

    # -- Focus handling --

    def action_focus_form(self) -> None:
        self.query_one("#repo-combo", FuzzyCombobox).focus_input()

    def on_workspace_panel_focus_add_repo_form(
        self, event: FocusAddRepoForm,
    ) -> None:
        event.stop()
        self.action_focus_form()

    # -- External status feedback --

    def notify_add_result(self, message: str, severity: str = "info") -> None:
        self._set_status(message, severity)
        if severity == "info":
            # Clear the form for the next add so the user can chain.
            # Also refresh candidates so a just-cloned repo shows as local.
            self._selected = None
            repo_combo = self.query_one("#repo-combo", FuzzyCombobox)
            repo_combo.set_value("")
            self.query_one("#base-branch-combo", FuzzyCombobox).clear()
            try:
                default = self.app.data.suggest_feature_branch(self._ticket_key)
                self.query_one("#feature-branch", Input).value = default
            except Exception:
                self.query_one("#feature-branch", Input).value = ""
            self._seeded = False
            self._seed_form()
            repo_combo.focus_input()

    def _set_status(self, message: str, severity: str) -> None:
        style = {
            "info": "green",
            "warn": "yellow",
            "error": "red",
        }.get(severity, "dim")
        try:
            self.query_one("#add-repo-status", Static).update(
                f"[{style}]{message}[/]"
            )
        except Exception:
            pass
