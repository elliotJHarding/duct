# duct-tui Specification

Mission control for agentic development. A Textual 8.x TUI that presents workspace state
and provides session management and action orchestration, built on top of the duct library.


## Architecture

**TUI = presentation + actuation. duct = data + state.**

The TUI never reads markdown files, calls subprocess, or manages state directly. If the TUI
needs data or capabilities that duct doesn't provide, augment duct — don't build it into the TUI.

**Workflow-agnostic.** Neither the TUI nor the duct package hardcodes workflow stages. The user
defines workflow prompts; the orchestrator follows them. The TUI presents whatever state exists.


## Package Structure

```
src/duct_tui/
    __init__.py
    app.py                  # DuctApp, entry point, mode switching
    theme.py                # Custom theme, semantic CSS variables
    data.py                 # Async data layer wrapping duct API
    actions.py              # Action execution (concrete + prompt dispatch)
    screens/
        __init__.py
        full.py             # FullScreen (default, tabbed workspaces)
    widgets/
        __init__.py
        ticket_list.py      # Priority-ordered ticket list (overview)
        session_panel.py    # Session list with status + controls
        action_panel.py     # Pending actions queue with approve/reject
        workspace_panel.py  # Repos, branches, dirty state per ticket
        pr_panel.py         # Pull requests + CI status per ticket
        attention_queue.py  # Global attention items needing human action
        sync_status.py      # Last sync times, manual sync trigger
        ticket_header.py    # Ticket summary bar (key, status, priority)
    modals/
        __init__.py
        launch_session.py   # Launch session modal (repo, prompt, flags)
        add_repo.py         # Add repo worktree modal
        confirm.py          # Generic confirmation modal
styles/
    app.tcss                # Global layout, theme variable usage
    full.tcss               # Full screen layout
```


## Data Layer

### duct API Additions

These functions must be added to `duct/api.py`, backed by shared library modules.
They return dataclass instances, not raw dicts or markdown.

```python
# --- Tickets ---

@dataclass(frozen=True)
class TicketSummary:
    key: str
    summary: str
    status: str                     # Jira status text
    category: str                   # Workflow category from sync
    priority: str
    priority_position: int | None   # Position in PRIORITY.md, None if absent
    pr_count: int
    ci_status: str                  # "passing" | "failing" | "mixed" | ""
    active_sessions: int
    dirty_repos: int
    pending_action_count: int
    path: Path

def get_tickets(root: Path) -> list[TicketSummary]:
    """All tracked tickets, priority-ordered."""

@dataclass(frozen=True)
class TicketDetail:
    ticket: Ticket                  # Existing duct model
    prs: list[PullRequest]          # Existing duct model
    repos: list[RepoStatus]
    sessions: list[SessionInfo]
    actions: list[Action]

def get_ticket_detail(root: Path, key: str) -> TicketDetail:
    """Full detail for a single ticket."""

# --- Repos ---

@dataclass(frozen=True)
class RepoStatus:
    name: str
    path: Path
    branch: str
    dirty: bool
    uncommitted_changes: int
    recent_commits: list[str]       # One-line summaries, most recent first

# --- Sessions ---

@dataclass(frozen=True)
class SessionInfo:
    session_id: str
    pid: int | None
    cwd: str
    ticket_key: str | None          # None for unlinked sessions
    status: str                     # "working" | "waiting" | "ready" | "planning" | "terminated"
    topic: str                      # Inferred goal / terminal tab title
    started_at: str                 # ISO 8601
    last_activity: str              # ISO 8601

def get_sessions(root: Path) -> list[SessionInfo]:
    """All Claude Code sessions on the machine, linked and unlinked."""

# --- Actions ---

@dataclass
class Action:
    id: str                         # UUID
    type: str                       # "concrete" | "prompt"
    description: str                # Human-readable summary
    status: str                     # "pending" | "approved" | "rejected"
    detail: dict                    # Type-specific payload (see Action Types)
    created_at: str                 # ISO 8601
    resolved_at: str | None

def get_actions(root: Path, key: str) -> list[Action]:
    """Pending and recent actions for a ticket."""

def resolve_action(root: Path, key: str, action_id: str, approved: bool) -> None:
    """Approve or reject a pending action."""

# --- Sync ---

@dataclass(frozen=True)
class SourceStatus:
    name: str                       # "jira" | "github" | "sessions" | "workspace" | "ci"
    last_synced: str | None         # ISO 8601
    stale: bool
    interval_seconds: int

def trigger_sync(root: Path, force: bool = False) -> list[SyncResult]:
    """Run sync coordinator. Returns results for sources that ran."""

def get_sync_status(root: Path) -> list[SourceStatus]:
    """Staleness info for all sync sources."""

# --- Terminal ---

def launch_session(
    root: Path,
    key: str,
    repo: str | None = None,
    prompt: str | None = None,
    extra_args: list[str] | None = None,
) -> int:
    """Launch a Claude Code session for a ticket. Returns PID.
    Extracts and reuses logic from session_cmd.session_start."""

def focus_session(pid: int) -> bool:
    """Switch terminal focus to the tab running the given PID.
    Extracts and reuses logic from session_cmd._focus_terminal_tab."""

def stop_session(pid: int) -> bool:
    """Send SIGTERM to a session. Returns True if signal sent."""
```

### Extraction from session_cmd.py

The following private functions in `duct/cli/session_cmd.py` contain reusable logic that
must be extracted to shared modules:

| Current location | Extract to | Functions |
|-----------------|-----------|-----------|
| `session_cmd.py` | `duct/session.py` | `discover_sessions()`, `infer_session_status()`, `extract_transcript_info()`, `match_session_ticket()`, `launch_session()`, `stop_session()` |
| `session_cmd.py` | `duct/terminal.py` | `focus_terminal_tab()`, `get_terminal_title()`, `get_tty()` |

After extraction, `session_cmd.py` becomes a thin CLI wrapper calling these shared functions.

### TUI Data Manager

```python
class DataManager:
    """Async wrapper around duct API. All methods use @work(thread=True)."""

    def __init__(self, root: Path):
        self.root = root

    async def load_tickets(self) -> list[TicketSummary]: ...
    async def load_ticket_detail(self, key: str) -> TicketDetail: ...
    async def load_sessions(self) -> list[SessionInfo]: ...
    async def load_actions(self, key: str) -> list[Action]: ...
    async def run_sync(self, force: bool = False) -> list[SyncResult]: ...
    async def get_sync_status(self) -> list[SourceStatus]: ...
    async def resolve_action(self, key: str, action_id: str, approved: bool) -> None: ...
    async def launch_session(self, key: str, repo: str | None, prompt: str | None) -> int: ...
    async def focus_session(self, pid: int) -> bool: ...
    async def stop_session(self, pid: int) -> bool: ...
```


## Actions and Escalations

### Storage Format

Per-ticket file: `{ticket_dir}/orchestrator/actions.yaml`

```yaml
actions:
  - id: "a1b2c3d4"
    type: concrete
    description: "Launch session to implement unit tests for PaymentService"
    status: pending
    detail:
      action: launch_session
      ticket_key: ERSC-1278
      prompt: "Write unit tests for the PaymentService class"
      repo: ice-claims
    created_at: "2026-03-27T10:00:00Z"
    resolved_at: null

  - id: "e5f6g7h8"
    type: prompt
    description: "Review and merge PR #42 — CI is green, reviews approved"
    status: pending
    detail:
      action: dispatch_prompt
      prompt: "Review PR #42 on ice-claims. CI green, reviews approved. Merge if appropriate."
    created_at: "2026-03-27T10:05:00Z"
    resolved_at: null
```

### Concrete Action Types

| action | detail fields | TUI behavior |
|--------|--------------|--------------|
| `launch_session` | `ticket_key`, `prompt`, `repo?` | Launch Claude Code via terminal adapter |
| `add_repo` | `ticket_key`, `repo_name`, `base_branch?` | Call duct workspace add-repo |
| `dispatch_prompt` | `prompt` | Launch orchestrator session with the prompt |

### Flow

- Orchestrator writes `actions.yaml` during its session
- TUI picks up pending actions via sync cycle
- User sees pending actions in ticket tab and global attention queue
- User presses `y` to approve, `n` to reject
- On approval of concrete action: TUI executes directly
- On approval of prompt action: TUI launches orchestrator with the prompt
- Action status updated to `approved`/`rejected` with `resolved_at` timestamp


## Terminal Adapter

### Protocol

```python
from typing import Protocol

class TerminalAdapter(Protocol):
    @property
    def name(self) -> str: ...

    def open_tab(self, cwd: Path, command: list[str], title: str | None = None) -> bool:
        """Open a new terminal tab, run command. Returns success."""

    def focus_tab(self, pid: int) -> bool:
        """Switch focus to the tab running the given PID."""

    def get_tab_title(self, pid: int) -> str | None:
        """Read the tab title for a session PID."""
```

### iTerm2 Adapter

Uses `osascript` (AppleScript). Reuses logic already in `session_cmd.py`:
- `_focus_terminal_tab()` for tab focusing via TTY matching
- `_get_terminal_title()` for reading tab titles
- New: `open_tab()` using iTerm2 AppleScript API to create tab + run command

### Wezterm Adapter (stub for v1)

Uses `wezterm cli` commands. Logic already partially exists in `session_cmd.py`:
- `wezterm cli list --format json` for pane discovery
- `wezterm cli activate-pane` for focus switching
- New: `wezterm cli spawn` for opening new tabs

### Configuration

```yaml
# config.yaml
terminal:
  adapter: iterm2       # "iterm2" | "wezterm"
```

Add `terminal_adapter: str = "iterm2"` to `WorkspaceConfig`.


## Screen Layout

### App Modes

Two modes, toggled with `m` key or auto-selected based on terminal width:
- **Full mode**: Default. Tabbed workspaces with overview + ticket tabs.
- **Compact mode**: For narrow terminals (< 60 columns) or split panes.

```python
class DuctApp(App):
    MODES = {"full": FullScreen}
```

### Full Mode — Overview Tab

```
┌─────────────────────────────────────────────────────────────────────────┐
│ duct                                                    Synced: 2m ago │
├─────────────────────────────────────────────────────────────────────────┤
│ Overview │ ERSC-1278 │ ERSC-1301 │                                     │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  TICKETS                                          SESSIONS              │
│  ─────────────────────────────────────────  ─────────────────────────── │
│  #1 ERSC-1278  Case File Updates            ● working  ERSC-1278       │
│     In Progress  PR:1 CI:✓  ● 1 session       Implementing tests      │
│     ⚠ 1 pending action                                                 │
│                                              ● ready   ERSC-1301       │
│  #2 ERSC-1301  API Rate Limiting               Waiting for review      │
│     Analysis Started  PR:0  ○ no sessions                              │
│                                              ○ idle    (unlinked)      │
│  #3 PS-442     Login Timeout Bug               ~/projects/scratch      │
│     In Progress  PR:1 CI:✗  ○ no sessions                             │
│     ⚠ 2 pending actions                                                │
│                                                                         │
│  ATTENTION                                                              │
│  ──────────────────────────────────────────────────                     │
│  ⚠ PS-442: CI failing on PR #87                                       │
│  ⚠ ERSC-1278: Action — "Launch test session"                          │
│  ⚠ PS-442: 2 actions pending                                          │
│                                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│ j/k Navigate  Enter Open  a Attention  s Sync  m Compact  ? Help       │
└─────────────────────────────────────────────────────────────────────────┘
```

Widget composition:

```
FullScreen
    Header()
    SyncStatusBar()
    TabbedContent(id="tabs")
        TabPane("Overview", id="overview")
            Horizontal()
                Vertical(id="left-col")         # 2fr
                    TicketList()
                    AttentionQueue()
                Vertical(id="right-col")        # 1fr
                    SessionPanel()
    Footer()
```

- **TicketList**: `OptionList` with Rich-formatted summaries. Each option shows key, summary,
  Jira status, PR/CI indicators, session count, pending action count.
- **AttentionQueue**: `OptionList` of items needing attention. Derived from ticket + session state.
  Enter navigates to the relevant ticket tab and focuses the relevant panel.
- **SessionPanel**: `OptionList` of all active sessions (linked + unlinked). Shows status symbol,
  ticket key (or cwd for unlinked), and topic. Enter jumps to terminal tab.

### Full Mode — Ticket Tab

Opened by pressing Enter on a ticket in the overview. Tabs are created on demand.

```
┌─────────────────────────────────────────────────────────────────────────┐
│ duct                                                    Synced: 2m ago │
├─────────────────────────────────────────────────────────────────────────┤
│ Overview │ ERSC-1278 │ ERSC-1301 │                                     │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ERSC-1278: Case File Updates                                           │
│  In Progress  Priority: #1  Story                                       │
│                                                                         │
│  ACTIONS (1 pending)                   SESSIONS                         │
│  ───────────────────────────────────── ──────────────────────────────── │
│  ⚠ Launch session to implement         ● working  PID 48291            │
│    unit tests for PaymentService         Implementing unit tests        │
│    [y] approve  [n] reject               Started: 10m ago              │
│                                                                         │
│  PULL REQUESTS                         WORKSPACE                        │
│  ───────────────────────────────────── ──────────────────────────────── │
│  #42 Add case file validation          ice-claims                       │
│  open  CI:✓  approved                    feature/ERSC-1278-case-file   │
│  @dev1: APPROVED                         clean  3 commits              │
│                                                                         │
│                                        ice-policy                       │
│                                          feature/ERSC-1278-case-file   │
│                                          dirty (2 changes)             │
│                                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│ y Approve  n Reject  l Launch  Enter Jump  r Add repo  w Close tab     │
└─────────────────────────────────────────────────────────────────────────┘
```

Widget composition:

```
TicketTab(ticket_key)
    TicketHeader(key, summary, status, priority, issue_type)
    Horizontal()
        Vertical(id="left-col")             # 1fr
            ActionPanel(key)
            PRPanel(key)
        Vertical(id="right-col")            # 1fr
            SessionPanel(key)
            WorkspacePanel(key)
```

- **TicketHeader**: Static widget showing ticket identity. Renders via `render()`.
- **ActionPanel**: `OptionList` of pending actions. Highlighted action shows description.
  `y`/`n` keys approve/reject. Approved actions execute immediately.
- **PRPanel**: `OptionList` of pull requests. Shows PR number, title, state, CI status,
  review status. Read-only (no GitHub interaction in v1).
- **SessionPanel**: Same widget as overview, filtered to this ticket. `l` launches new session.
  `x` stops selected session (with confirmation). Enter jumps to terminal tab.
- **WorkspacePanel**: `OptionList` of repos. Shows repo name, branch, dirty/clean indicator,
  commit count. `r` opens Add Repo modal.

### Compact Mode

For narrow terminals or split panes. Two sub-modes, toggled with `t` key:

**Ticket-focused** (default):

```
┌────────────────────────────────────────┐
│ duct                     Synced: 2m   │
├────────────────────────────────────────┤
│ ERSC-1278  Case File Updates           │
│ In Progress  PR:1 CI:✓  ● 1 session  │
│ ⚠ 1 action pending                    │
│                                        │
│ ERSC-1301  API Rate Limiting           │
│ Analysis  PR:0  ○ no sessions          │
│                                        │
│ PS-442  Login Timeout Bug              │
│ In Progress  PR:1 CI:✗               │
│ ⚠ 2 actions pending                   │
├────────────────────────────────────────┤
│ j/k Nav  Enter Open  a Attn  t Sess   │
└────────────────────────────────────────┘
```

**Session-focused** (toggled):

```
┌────────────────────────────────────────┐
│ duct sessions            Synced: 2m   │
├────────────────────────────────────────┤
│ ● working  ERSC-1278                   │
│   Implementing unit tests   PID 48291  │
│                                        │
│ ● ready    ERSC-1301                   │
│   Waiting for review        PID 48305  │
│                                        │
│ ○ idle     (unlinked)                  │
│   ~/projects/scratch        PID 49001  │
├────────────────────────────────────────┤
│ j/k Nav  Enter Jump  t Tickets         │
└────────────────────────────────────────┘
```


## Tab Management

- Overview tab is always present and cannot be closed
- Ticket tabs are created on demand when a ticket is opened (Enter in overview or attention queue)
- If a tab for the ticket already exists, switch to it instead of creating a duplicate
- `w` closes the current ticket tab and returns to overview
- Number keys `1`-`9` switch to tab by position
- `]`/`[` cycle next/previous tab


## Keybindings

### Global (App-level)

| Key | Action | Description |
|-----|--------|-------------|
| `a` | `next_attention` | Jump to next item needing attention |
| `s` | `sync` | Force sync all sources |
| `1`-`9` | `focus_tab` | Switch to tab by position |
| `]` | `next_tab` | Next tab |
| `[` | `prev_tab` | Previous tab |
| `?` | `show_help` | Show keybinding help |
| `q` | `quit` | Quit (confirm if active sessions) |

### Overview — TicketList

| Key | Action | Description |
|-----|--------|-------------|
| `j` / `down` | `cursor_down` | Next ticket |
| `k` / `up` | `cursor_up` | Previous ticket |
| `enter` | `open_ticket` | Open ticket in new tab |
| `l` / `right` | `focus_sessions` | Move focus to sessions panel |

### Overview — SessionPanel

| Key | Action | Description |
|-----|--------|-------------|
| `j` / `down` | `cursor_down` | Next session |
| `k` / `up` | `cursor_up` | Previous session |
| `enter` | `jump_to_session` | Focus terminal tab for this session |
| `h` / `left` | `focus_tickets` | Move focus to ticket list |

### Overview — AttentionQueue

| Key | Action | Description |
|-----|--------|-------------|
| `j` / `down` | `cursor_down` | Next item |
| `k` / `up` | `cursor_up` | Previous item |
| `enter` | `open_item` | Navigate to relevant ticket tab + panel |

### Ticket Tab — ActionPanel

| Key | Action | Description |
|-----|--------|-------------|
| `y` | `approve_action` | Approve selected action |
| `n` | `reject_action` | Reject selected action |

### Ticket Tab — SessionPanel

| Key | Action | Description |
|-----|--------|-------------|
| `enter` | `jump_to_session` | Focus terminal tab |
| `l` | `launch_session` | Open launch session modal |
| `x` | `stop_session` | Stop session (with confirmation) |

### Ticket Tab — WorkspacePanel

| Key | Action | Description |
|-----|--------|-------------|
| `r` | `add_repo` | Open add repo modal |

### Ticket Tab — Navigation

| Key | Action | Description |
|-----|--------|-------------|
| `tab` | `focus_next_panel` | Cycle focus between panels |
| `shift+tab` | `focus_prev_panel` | Cycle focus backwards |
| `w` | `close_tab` | Close this ticket tab |

### Binding Implementation

Bindings live on the widget that handles them. Textual's Footer auto-discovers bindings
from the focus chain. Vim keys (`j`/`k`) are set with `show=False` since they duplicate
arrow key behavior.

```python
class TicketList(OptionList):
    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("enter", "open_ticket", "Open"),
        Binding("l", "focus_sessions", "Sessions"),
    ]
```


## Theme and Styling

### Philosophy

Terminal-native: respect the user's chosen terminal colors for backgrounds, foregrounds,
and base UI elements. Inject duct identity through a single brand accent color (`#bb9af7`
lavender) used sparingly on panel titles and focus indicators. Status colors use ANSI names
so they adapt to whatever palette the terminal provides.

### Custom Theme

```python
def create_duct_theme() -> Theme:
    return Theme(
        name="duct",
        primary="ansi_blue",
        secondary="ansi_cyan",
        accent="#bb9af7",              # Duct brand lavender — the ONE hex color
        foreground="ansi_default",     # Inherit terminal foreground
        background="ansi_default",     # Inherit terminal background
        surface="ansi_default",        # No imposed panel fills
        panel="ansi_bright_black",     # Subtle border from terminal palette
        boost="ansi_default",
        success="ansi_green",
        warning="ansi_yellow",
        error="ansi_red",
        dark=True,
        variables={
            "agent-working": "ansi_blue",
            "agent-ready": "ansi_green",
            "agent-waiting": "ansi_yellow",
            "agent-planning": "ansi_magenta",
            "agent-terminated": "ansi_bright_black",
            "ci-passing": "ansi_green",
            "ci-failing": "ansi_red",
            "ci-pending": "ansi_yellow",
            "attention": "ansi_bright_red",
        },
    )
```

### Status Symbols

| State | Symbol | Color | Rich style |
|-------|--------|-------|------------|
| Session working | `●` | `$agent-working` | `blue` |
| Session ready | `●` | `$agent-ready` | `green` |
| Session waiting | `●` | `$agent-waiting` | `yellow` |
| Session planning | `●` | `$agent-planning` | `magenta` |
| Session terminated | `○` | `$agent-terminated` | `bright_black` |
| CI passing | `✓` | `$ci-passing` | `green` |
| CI failing | `✗` | `$ci-failing` | `red` |
| CI pending | `◌` | `$ci-pending` | `yellow` |
| Action pending | `⚠` | `$attention` | `bright_red` |
| Repo dirty | `△` | `$warning` | `yellow` |
| Repo clean | `✓` | `$success` | `green` |

The "Rich style" column shows the ANSI color name used in Rich Text markup within
widgets (where CSS variables are not available).

### TCSS Approach

- External `.tcss` files, never inline CSS strings
- No hardcoded hex values — use theme CSS variables or ANSI color names
- `app.tcss` for global layout; per-screen files for screen-specific styles
- No explicit `background` on panels/headers — let terminal background show through
- Use `text-style: dim` for muted text (not `color: $text-muted`, which is a no-op under ANSI themes)
- Brand accent (`$accent`) reserved for panel titles and focus borders
- Asymmetric layout: content areas get more space than navigation
- Generous padding to avoid cramped feel

```css
/* styles/app.tcss — example */
Screen { background: $background; }

SyncStatusBar {
    dock: top;
    height: 1;
    text-style: dim;
    padding: 0 2;
}

.panel-title {
    text-style: bold;
    color: $accent;
    margin: 1 0 0 0;
}

.attention-item {
    color: $attention;
    text-style: bold;
}
```


## Data Flow and Refresh

### Sync Cycle

- `DuctApp.on_mount()` triggers initial data load and starts repeating timers
- Session refresh: every 15 seconds (configurable via `syncIntervals.sessions`)
- Full sync: every 5 minutes (duct's staleness logic skips sources that aren't stale)
- Manual sync: `s` key (respects staleness), `S` key (forces all sources)
- All sync calls use `@work(thread=True)` — never block the event loop
- On sync completion, post a custom `SyncComplete` message to update widgets

### Reactive State

```python
class DuctApp(App):
    tickets: reactive[list[TicketSummary]] = reactive(list)
    sessions: reactive[list[SessionInfo]] = reactive(list)
```

Watchers on these reactives update widgets via `widget.update()`, `clear_options()` /
`add_option()`. No full recompose — widgets update in place.

### Attention Queue Derivation

The attention queue is computed from current state, not stored. Items include:
- Tickets with pending actions
- Tickets with failing CI
- Sessions in "waiting" status (blocked on human input)

The `a` key cycles through these items globally, opening the relevant ticket tab
and focusing the relevant panel.


## Session Lifecycle

### Launch

```
User presses `l` in ticket tab SessionPanel
    -> LaunchSessionModal opens
    -> User selects repo (from ticket's worktrees), optional prompt
    -> DataManager.launch_session(key, repo, prompt)
    -> duct.launch_session() builds claude command with:
        - --add-dir ticket_dir
        - sandbox flags from config
        - optional -p prompt
        - working directory = repo worktree or ticket dir
    -> TerminalAdapter.open_tab(cwd, command, title)
    -> New terminal tab opens with Claude Code
    -> TUI shows notify("Session launched for ERSC-1278")
```

### Jump

```
User presses Enter on a session in SessionPanel
    -> DataManager.focus_session(pid)
    -> duct.focus_session() resolves PID -> TTY -> terminal tab
    -> TerminalAdapter.focus_tab(pid)
    -> Terminal switches to that tab
    -> TUI remains running in its own tab
```

### Stop

```
User presses `x` on a session
    -> ConfirmModal: "Stop session PID 48291?"
    -> On confirm: DataManager.stop_session(pid)
    -> duct.stop_session() sends SIGTERM
    -> Session status updates on next sync cycle
```


## Modals

### Launch Session

```
┌──────────────── Launch Session ─────────────────┐
│                                                   │
│  Ticket: ERSC-1278 — Case File Updates            │
│                                                   │
│  Repo:   [ice-claims              ▾]             │
│  Prompt: [                              ]         │
│                                                   │
│           [Launch]    [Cancel]                     │
│                                                   │
└───────────────────────────────────────────────────┘
```

- `ModalScreen[LaunchConfig | None]`
- Repo dropdown populated from ticket's workspace repos
- Prompt is optional free-text (pre-filled when launched from an approved action)
- On Launch: returns `LaunchConfig(ticket_key, repo, prompt)` to caller

### Add Repo

```
┌─────────────── Add Repository ──────────────────┐
│                                                   │
│  Ticket: ERSC-1278 — Case File Updates            │
│                                                   │
│  Repository:     [ice-claims           ▾]        │
│  Base Branch:    [main                 ▾]        │
│  Feature Branch: feature/ERSC-1278-case-file      │
│                                                   │
│           [Add]       [Cancel]                     │
│                                                   │
└───────────────────────────────────────────────────┘
```

- `ModalScreen[AddRepoConfig | None]`
- Repository dropdown: repos discovered from `config.repo_paths`
- Base branch dropdown: populated when repo selected
- Feature branch: auto-generated from ticket key + summary, editable
- On Add: calls duct workspace add-repo equivalent


## Application Entry Point

```python
class DuctApp(App):
    TITLE = "duct"
    CSS_PATH = ["../styles/app.tcss", "../styles/full.tcss"]
    MODES = {"full": FullScreen}
    BINDINGS = [
        Binding("a", "next_attention", "Attention"),
        Binding("s", "sync", "Sync"),
        Binding("question_mark", "show_help", "Help"),
        Binding("q", "quit", "Quit"),
    ]

    tickets = reactive(list)
    sessions = reactive(list)

    def __init__(self):
        super().__init__()
        self.register_theme(create_duct_theme())
        self.theme = "duct"
        root = find_workspace_root()
        self.data = DataManager(root)

    def on_mount(self) -> None:
        self.switch_mode("full")
        self._load_initial_data()
        self.set_interval(15.0, self._refresh_sessions)
        self.set_interval(300.0, self._periodic_sync)


def main():
    app = DuctApp()
    app.run()
```


## Session Configuration

Session launch defaults are global in `config.yaml`:

```yaml
session:
  skipPermissions: false        # --dangerously-skip-permissions (requires sandbox)
  extraArgs: []                 # Additional flags passed to claude CLI
```

These defaults apply to all sessions launched from the TUI. Per-session overrides
are possible via the launch modal's prompt field and duct CLI passthrough args.


## Artifact Rendering

`ArtifactView` extracts ` ```mermaid ` fenced blocks from artifact markdown and renders
them as PNG images via `mermaid-cli` (`mmdc`) + `textual-image`. Diagrams are cached by
sha256 of the source under `~/.cache/duct/mermaid/` so repeat views are instant. If
`mmdc` is not on PATH, mermaid blocks fall back to code-block rendering and a one-shot
notify fires. Install `mmdc` with `npm i -g @mermaid-js/mermaid-cli`.

Very large mermaid diagrams can feel sluggish to scroll — `textual-image`'s Sixel cache
is keyed on the visible crop region, so every scroll step re-encodes the Sixel payload
from source. mmdc is invoked with `-w 1400` to keep typical diagrams in check; diagrams
that still expand to thousands of pixels on either axis will emit large Sixel streams
per frame.


## Non-Goals (v1)

Explicitly excluded from this version:

- Config editing in TUI — edit config.yaml externally
- Artifact editing — BACKGROUND.md, AC.md, etc. are read-only in TUI
- Jira interaction — no transitions, comments, or updates
- GitHub interaction — no PR creation, review, or merge
- Command palette — architecture supports it (Textual built-in), deferred
- Multi-workspace — one workspace root per TUI instance
- Log/transcript streaming — sessions observed via status, not raw output
- Workflow stage visualization — the system is workflow-agnostic


## Implementation Phases

### Phase 1: Foundation
- Package structure and DuctApp skeleton
- Theme and TCSS files
- Add required API functions to duct package (extract from session_cmd.py)
- DataManager with thread workers
- FullScreen with TabbedContent shell (overview tab only)

### Phase 2: Overview
- TicketList widget with priority-ordered display
- SessionPanel widget showing all sessions
- AttentionQueue derived from ticket + session state
- SyncStatusBar with manual sync trigger
- Periodic refresh via timers

### Phase 3: Ticket Tabs
- Ticket tab with four-panel layout
- PRPanel, WorkspacePanel, ActionPanel, SessionPanel per-ticket
- Tab lifecycle (open, close, switch, dedup)
- TicketHeader with ticket identity

### Phase 4: Session Management
- TerminalAdapter protocol and iTerm2Adapter
- Launch session modal and flow
- Jump-to-session via terminal adapter
- Stop session with confirmation

### Phase 5: Actions
- actions.yaml read/write in duct package
- Action approval flow (approve -> execute)
- Concrete action execution (launch session, add repo)
- Prompt action dispatch to orchestrator

### Phase 6: Polish
- Compact mode screen (ticket + session views)
- Add repo modal
- Responsive layout adjustments
- Error handling and notify() feedback
- Help overlay
