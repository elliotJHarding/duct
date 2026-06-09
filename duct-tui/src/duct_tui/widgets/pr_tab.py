"""PRTab -- top-level pull request view with My PRs / Needs Review sections.

My PRs groups the current user's PRs into three sub-sections (Needs action,
Waiting for review, Done) via section-header rows inside a single scrollable
OptionList. Row rendering mirrors the overview card via `pr_render.render_pr_row`.
"""

from __future__ import annotations

import webbrowser

from rich.text import Text
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.message import Message
from textual.widget import Widget
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from duct.models import PullRequest
from duct.pr import categorize_my_pr, pr_action_reasons
from duct_tui.avatar_cache import render_avatar
from duct_tui.icons import UNICODE, Icons
from duct_tui.widgets.pr_render import format_relative, render_pr_row
from duct_tui.widgets.vim_mixin import VimListMixin


class PRListPanel(VimListMixin, OptionList):
    """OptionList displaying PRs with ticket association.

    The list can contain disabled header and separator rows. `_entries` is
    maintained in parallel with the options: indices that correspond to a
    real PR hold a `(ticket_key, PullRequest)` tuple; header/separator rows
    hold `None`.
    """

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("enter", "open_in_browser", "Open"),
        Binding("d", "deep_review", "Deep review"),
        Binding("t", "goto_ticket", "Ticket"),
        *VimListMixin.VIM_BINDINGS,
    ]

    class PROpened(Message):
        def __init__(self, url: str) -> None:
            super().__init__()
            self.url = url

    class TicketJump(Message):
        def __init__(self, ticket_key: str) -> None:
            super().__init__()
            self.ticket_key = ticket_key

    def __init__(self, *, show_avatars: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self._entries: list[tuple[str, PullRequest] | None] = []
        self._sep_seq: int = 0
        self._skip_direction: int = 1
        self._icons: Icons = UNICODE
        self._show_avatars: bool = show_avatars
        # Last rendered payload, kept so avatar-download completions can
        # trigger a transparent re-render once the PNG lands on disk.
        self._last_review_entries: list[tuple[str, PullRequest]] | None = None

    def on_mount(self) -> None:
        self._icons = getattr(self.app, "icons", UNICODE)

    # ------------------------------------------------------------------ helpers

    def _reset(self) -> None:
        self._entries = []
        self._sep_seq = 0
        self.clear_options()

    def _add_empty_state(self, label: str = "No pull requests") -> None:
        self.add_option(Option(Text(label, style="dim"), id="empty", disabled=True))
        self._entries.append(None)

    def _add_section_header(self, label: str, count: int, bucket_id: str) -> None:
        text = Text()
        text.append("── ", style="bold cyan")
        text.append(label, style="bold cyan")
        text.append(f"  ({count})", style="dim")
        self.add_option(Option(text, id=f"header-{bucket_id}", disabled=True))
        self._entries.append(None)

    def _add_separator(self) -> None:
        self._sep_seq += 1
        self.add_option(
            Option(Text(""), id=f"sep-{self._sep_seq}", disabled=True),
        )
        self._entries.append(None)

    def _add_pr_row(
        self,
        ticket_key: str,
        pr: PullRequest,
        *,
        show_author: bool = False,
        collapsed: bool = False,
        relative_time_str: str = "",
        action_reasons: list[str] | None = None,
        with_avatar: bool = False,
    ) -> None:
        avatar = None
        if with_avatar:
            avatar_url = getattr(pr, "author_avatar_url", None)
            avatar = render_avatar(pr.author, avatar_url, self._on_avatar_ready)
        rendered = render_pr_row(
            pr,
            self._icons,
            ticket_key=ticket_key or None,
            show_author=show_author,
            relative_time_str=relative_time_str or format_relative(pr.updated_at),
            action_reasons=tuple(action_reasons or ()),
            compact=False,
            collapsed=collapsed,
            avatar=avatar,
        )
        self.add_option(Option(rendered, id=f"{pr.repo}#{pr.number}:{ticket_key}"))
        self._entries.append((ticket_key, pr))

    def _on_avatar_ready(self) -> None:
        """Re-render the review list once an avatar download completes."""
        if self._last_review_entries is None:
            return
        try:
            self.app.call_from_thread(self._rerender_review_list)
        except RuntimeError:
            # No running app (e.g. during tests) — nothing to refresh.
            pass

    def _rerender_review_list(self) -> None:
        if self._last_review_entries is not None:
            self.update_review_list(self._last_review_entries)

    # ------------------------------------------------------------------ public

    def update_my_prs(
        self,
        needs_action: list[tuple[str, PullRequest]],
        waiting: list[tuple[str, PullRequest]],
        done: list[tuple[str, PullRequest]],
    ) -> None:
        """Render the My PRs panel with three grouped sub-sections."""
        self._reset()
        if not (needs_action or waiting or done):
            self._add_empty_state()
            return

        sections = [
            ("Needs action", "needs-action", needs_action, False),
            ("Waiting for review", "waiting", waiting, False),
            ("Done", "done", done, True),
        ]

        first = True
        for label, bucket_id, entries, collapsed in sections:
            if not entries:
                continue
            if not first:
                self._add_separator()
            first = False
            self._add_section_header(label, len(entries), bucket_id)
            self._add_separator()

            # Cluster PRs by ticket key while preserving the caller's sort
            # order: first-appearance of a key anchors the group, so the
            # ticket with the most recent activity stays on top.
            clustered: list[tuple[str, PullRequest]] = []
            groups: dict[str, list[tuple[str, PullRequest]]] = {}
            for ticket_key, pr in entries:
                groups.setdefault(ticket_key, []).append((ticket_key, pr))
            for group in groups.values():
                clustered.extend(group)

            for i, (ticket_key, pr) in enumerate(clustered):
                reasons = pr_action_reasons(pr) if bucket_id == "needs-action" else []
                self._add_pr_row(
                    ticket_key,
                    pr,
                    collapsed=collapsed,
                    action_reasons=reasons,
                )
                # Collapsed (done) rows pack together with no separator gaps.
                if not collapsed and i < len(clustered) - 1:
                    self._add_separator()

    def update_review_list(self, entries: list[tuple[str, PullRequest]]) -> None:
        """Render the flat 'Needs my review' list."""
        self._reset()
        self._last_review_entries = list(entries)
        if not entries:
            self._add_empty_state()
            return

        for i, (ticket_key, pr) in enumerate(entries):
            done = pr.state in ("merged", "closed")
            self._add_pr_row(
                ticket_key,
                pr,
                show_author=True,
                collapsed=done,
                with_avatar=self._show_avatars and not done,
            )
            # Pack consecutive collapsed (done) rows together; gap otherwise.
            next_done = (
                i + 1 < len(entries) and entries[i + 1][1].state in ("merged", "closed")
            )
            if i < len(entries) - 1 and not (done and next_done):
                self._add_separator()

    # ------------------------------------------------------------------ actions

    def _entry_at(self, idx: int | None) -> tuple[str, PullRequest] | None:
        if idx is None or idx >= len(self._entries):
            return None
        return self._entries[idx]

    def action_open_in_browser(self) -> None:
        entry = self._entry_at(self.highlighted)
        if entry is None:
            return
        _ticket_key, pr = entry
        if not pr.url:
            return
        # Open directly so Enter works regardless of which screen hosts us,
        # then also post the message for anything else that wants to listen.
        try:
            webbrowser.open(pr.url)
        except Exception:
            pass
        self.post_message(self.PROpened(pr.url))

    def action_deep_review(self) -> None:
        entry = self._entry_at(self.highlighted)
        if entry is None:
            return
        _ticket_key, pr = entry
        if not pr.repo or not pr.branch:
            self.app.notify(
                "PR is missing repo or branch info; cannot deep review.",
                severity="warning",
            )
            return
        repo_name = pr.repo.split("/", 1)[-1]
        self.app.notify(f"Preparing {repo_name}@{pr.branch} for review…")
        self._run_deep_review(pr, repo_name)

    @work(thread=True, exclusive=False)
    def _run_deep_review(self, pr: PullRequest, repo_name: str) -> None:
        try:
            self.app.data.do_deep_review(pr)
        except Exception as exc:  # noqa: BLE001 — surface any failure to the UI
            self.app.call_from_thread(
                self.app.notify,
                f"Deep review failed: {exc}",
                severity="error",
                timeout=10,
            )
            return
        self.app.call_from_thread(
            self.app.notify,
            f"Opened {repo_name}@{pr.branch} in IntelliJ",
        )

    def action_goto_ticket(self) -> None:
        entry = self._entry_at(self.highlighted)
        if entry is None:
            return
        ticket_key, _pr = entry
        if not ticket_key:
            return  # no ticket key at all — nothing to open (e.g. NOJIRA PRs)
        self.post_message(self.TicketJump(ticket_key))

    # ----------------------------------------------- disabled-row skip support

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted,
    ) -> None:
        """Auto-skip disabled header/separator/empty rows on highlight."""
        if event.option is None:
            return
        option_id = event.option.id or ""
        if not (
            option_id.startswith("header-")
            or option_id.startswith("sep-")
            or option_id == "empty"
        ):
            return
        idx = self.highlighted
        if idx is None:
            return
        target = idx + self._skip_direction
        # Clamp; if we can't advance any further, bail out (don't wrap or loop).
        if target < 0 or target >= self.option_count:
            return
        self.highlighted = target

    def action_cursor_down(self) -> None:
        self._skip_direction = 1
        super().action_cursor_down()

    def action_cursor_up(self) -> None:
        self._skip_direction = -1
        super().action_cursor_up()

    def _vim_goto_first(self) -> None:
        if self.option_count:
            self._skip_direction = 1
            self.highlighted = 0

    def _vim_goto_last(self) -> None:
        if self.option_count:
            self._skip_direction = -1
            self.highlighted = self.option_count - 1


class PRTab(Widget):
    """Top-level PR tab with My PRs and Needs Review sections."""

    DEFAULT_CSS = """
    PRTab {
        height: 1fr;
    }
    #pr-panels {
        height: 1fr;
    }
    PRTab PRListPanel {
        width: 1fr;
        height: 1fr;
        border: round $panel;
    }
    PRTab PRListPanel:focus {
        border: round $accent;
    }
    """

    BINDINGS = [
        Binding("h", "focus_left", "Left pane", show=False),
        Binding("l", "focus_right", "Right pane", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Horizontal(id="pr-panels"):
            yield PRListPanel(id="my-prs")
            yield PRListPanel(id="needs-review", show_avatars=True)

    def on_mount(self) -> None:
        self.query_one("#my-prs", PRListPanel).border_title = "My PRs"
        self.query_one("#needs-review", PRListPanel).border_title = "Needs my review"
        self.refresh_data()

    @work(thread=True)
    def refresh_data(self) -> None:
        all_prs = self.app.data.load_all_prs()
        username = self.app.data.get_github_username()
        self.app.call_from_thread(self._apply_data, all_prs, username)

    def _apply_data(
        self,
        all_prs: list[tuple[str, PullRequest]],
        username: str | None,
    ) -> None:
        my_panel = self.query_one("#my-prs", PRListPanel)
        review_panel = self.query_one("#needs-review", PRListPanel)

        if not username:
            # Without a username we can't filter. Show everything in My PRs
            # as a flat "waiting" bucket so the user still sees their PRs.
            my_panel.update_my_prs([], list(all_prs), [])
            review_panel.update_review_list([])
            return

        needs_action: list[tuple[str, PullRequest]] = []
        waiting: list[tuple[str, PullRequest]] = []
        done: list[tuple[str, PullRequest]] = []
        for k, p in all_prs:
            if p.author != username:
                continue
            bucket = categorize_my_pr(p)
            if bucket == "needs_action":
                needs_action.append((k, p))
            elif bucket == "waiting_for_review":
                waiting.append((k, p))
            else:
                done.append((k, p))

        needs_action.sort(key=lambda kp: kp[1].updated_at, reverse=True)
        waiting.sort(key=lambda kp: kp[1].updated_at, reverse=True)
        # Done: merged first, then closed, each sub-group by updated_at desc.
        merged = sorted(
            (x for x in done if x[1].state == "merged"),
            key=lambda kp: kp[1].updated_at,
            reverse=True,
        )
        closed = sorted(
            (x for x in done if x[1].state != "merged"),
            key=lambda kp: kp[1].updated_at,
            reverse=True,
        )
        done = merged + closed

        # Needs-review: GitHub's review-requested search flagged this PR as
        # needing my review (personally or via a team I belong to). The flag is
        # set during sync — it sees team requests that `requested_reviewers`
        # (User-only) cannot. Still guard out my own PRs defensively.
        review_prs = [
            (k, p)
            for k, p in all_prs
            if p.needs_my_review and p.author != username
        ]
        review_prs.sort(key=lambda kp: kp[1].updated_at, reverse=True)

        my_panel.update_my_prs(needs_action, waiting, done)
        review_panel.update_review_list(review_prs)

    def action_focus_left(self) -> None:
        self.query_one("#my-prs").focus()

    def action_focus_right(self) -> None:
        self.query_one("#needs-review").focus()
