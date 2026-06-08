# DUCT TODO

## Issues

- Ticket page
  - Ticket summary could display more useful information at a glance (Agent D added assignee · issue_type · priority — confirm sufficient)


## Fix Done

- Orchestrator summary
  - Renders markdown richly via dual path (Textual `Markdown` full, `Static`+`RichMarkdown` under `app.fast_markdown`) — mirrors `ArtifactView` pattern
  - Summary section capped: `#conductor max-height: 8`, `#conductor-message-container max-height: 6` (scrollable) — run log always visible
  - 200-char truncation dropped
  - `orchestrator.md` prompt: summary must be 2-3 sentences max, no headings, no bullets

- Overview tab
  - Artifacts render in two columns, each truncated to ~22 chars with `…`
  - Session rows now delegate to `session_panel.render_session_card` (2 lines: mode/topic + status/rel/cwd) so the overview matches the Sessions tab and Ticket-tab Summary pane
  - `render_session_card` gained `show_ticket` — overview card and ticket summary pane pass `show_ticket=False` (ticket badge is redundant when the pane is already scoped to one ticket)
  - `_compute_section_heights` bumped sessions from `n` to `n * 2` to reflect the 2-line card
  - Header: Jira status appended after badge with `·` separator; numeric priority `#N` removed
  - Assignee shown for any non-empty, non-"Unassigned" value (dim, on header row)
  - `TicketOverview.assignee` plumbed through `models.py` + `api.get_ticket_overviews`
  - `ticket_card_list._compute_section_heights` updated: artifacts `ceil(n/2)`, PRs still `3n-1`

- PRs tab
  - GitHub avatars on "Needs my review" rows via `textual_image.renderable.HalfcellImage` in a Rich table cell; disk cache at `~/.cache/duct/avatars/`; fallback = per-login coloured initials badge
  - Enter now opens PR in browser via `webbrowser.open(pr.url)` (still posts `PROpened`)
  - 3-line PR cards on PRs tab (title / `{repo}  #{number}  @author` / `{state} {CI} {review} {age}`); `pr_panel.py` stays compact
  - Ticket-key strip regex broadened: covers `KEY:`, `KEY -`, `KEY —`, `KEY |`, `[KEY]`, `(KEY)`, bare `KEY foo`
  - Added `author_avatar_url` to `PullRequest` model, populated from GraphQL `avatarUrl`

- Ticket page
  - Highlighting the "ticket" summary row now shows TICKET.md via a second `ArtifactView` (reuses fast_markdown + mermaid paths) — replaces metadata-only `TicketDetailView`
  - `TicketDetailView` module, `widgets/__init__` export, and TCSS selectors removed

- Sync
  - `SyncIntervals` defaults lowered: `jira`, `github`, `ci` all `10800s → 600s` (10 min); sessions/workspace unchanged
  - `SyncCoordinator.run()` exception-safe: raised exceptions from a source become `SyncResult(errors=[...])`, timestamp only advances on no errors, other sources continue
  - New `tests/sync/test_base.py` covering timestamp-on-success semantics


## Verified working by user

- Orchestrator summary window no longer covers the run log (max-height cap applied after user feedback)
