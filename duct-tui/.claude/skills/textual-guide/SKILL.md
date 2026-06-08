---
name: textual-guide
description: >
  Comprehensive guide for building terminal UIs with the Textual framework (8.x).
  Use this skill whenever working on Textual TUI code, creating or modifying terminal
  user interfaces, working in the duct-tui project, or when the user asks about
  Textual widgets, styling, layout, focus, or theming. Also trigger when you see
  imports from textual, .tcss files, or App/Screen/Widget subclasses.
---

# Textual 8.x Framework Guide

This guide captures the essential patterns and built-in features of Textual 8.x for building
professional terminal UIs. The core principle: **use the framework, don't fight it**. Textual
provides built-in solutions for most common TUI patterns — using them gives you keyboard
navigation, focus management, styling, and accessibility for free.


## 1. TUI Design Principles

Design intent before code. Before writing any widget, ask: what is this screen's purpose?
What tone should it convey? A live dashboard has different visual language than a settings form.

### Color as Information, Not Decoration

Dominant neutral palette with sharp semantic accents. Status encoding via `$success`/`$warning`/
`$error`. Avoid evenly-distributed color — if everything is colorful, nothing stands out.

```css
/* GENERIC — color everywhere, nothing stands out */
#sidebar { background: $primary; }
#content { background: $secondary; }
#footer { background: $accent; }

/* INTENTIONAL — neutral base, color marks what matters */
#sidebar { background: $surface; }
#content { background: $background; padding: 1 4; }
.status-active { color: $success; }
.status-error { color: $error; text-style: bold; }
```

### Typography in the Terminal

Bold for headings and active items. Dim for secondary info. Italic sparingly. Information
hierarchy through text weight, not color alone:

```python
# Level 1: Bold + primary color (active/selected items)
Text.from_markup(f"[bold $primary]{title}[/]")
# Level 2: Normal weight (standard content)
Text.from_markup(f"{description}")
# Level 3: Dim (timestamps, metadata, secondary info)
Text.from_markup(f"[dim]{timestamp}[/]")
# Level 4: Muted (disabled, inactive)
Text.from_markup(f"[$text-muted]{inactive_label}[/]")
```

### Unicode Symbol Reference

Use Unicode symbols (not emoji) for status indicators. Consistent symbols across the app:

| Purpose | Symbols | Notes |
|---------|---------|-------|
| Success/complete | `✓` `✔` | Light vs heavy check |
| Failure/error | `✗` `✘` `×` | Cross marks |
| Warning | `⚠` `△` | Triangle warning |
| Running/active | `●` `◉` | Filled circle |
| Pending/inactive | `○` `◌` | Empty circle |
| Arrow navigation | `→` `←` `↑` `↓` | Directional |
| Chevron/expand | `▸` `▾` `▹` `▿` | Collapsible indicators |
| Separator | `│` `─` `┃` `━` | Box-drawing chars |
| Bullet | `•` `◦` `▪` `▫` | List markers |
| Ellipsis | `…` | Truncation |

### Spatial Composition

Asymmetric layouts feel intentional. Wide content area + narrow sidebar, not uniform 50/50.
Generous padding — terminal UIs feel cramped by default:

```css
#sidebar { dock: left; width: 36; }
#content { width: 1fr; padding: 1 4; }

/* NOT this — uniform splits feel generic */
#left { width: 1fr; }
#right { width: 1fr; }
```

### Atmosphere and Depth

Use `$surface` vs `$background` vs `$panel` to create visual layers. Border styles
communicate containment hierarchy. Dim/muted text pushes secondary info back:

```css
Screen { background: $background; }
#sidebar { background: $surface; border-right: solid $panel; }
#detail-panel { background: $surface; border: round $panel; }
```


## 2. UX Patterns for Terminal Applications

Patterns extracted from successful Textual apps (Posting, Harlequin, Dolphie, Toolong).

### Keyboard-First Interaction

Design around keyboard, mouse secondary. Every visible action should have a keybinding.
The `Footer` widget makes bindings discoverable — it auto-updates as focus changes.

### Progressive Disclosure

Don't show everything at once. Use `TabbedContent` for parallel views, `Collapsible` for
optional detail, modal screens for focused workflows. Overview first, drill into detail.

### Non-Blocking Feedback

Never freeze the UI. Use `self.notify()` for transient status messages:

```python
self.notify("File saved", severity="information")
self.notify("Connection lost", severity="error", timeout=10)
# Severity levels: "information", "warning", "error"
```

Use `LoadingIndicator` for async operations. Stream partial results rather than waiting.

### Context-Aware Navigation

Bindings change with focus. Design binding sets per-widget, not globally. When the sidebar
is focused, `j`/`k` navigate items. When content is focused, `j`/`k` scroll. The Footer
reflects this automatically.

### Visual Hierarchy and Reading Order

Brightest/boldest = primary action or status. Aim for 3-4 levels of emphasis:
1. Bold + `$primary` — active item, current selection
2. Normal weight `$foreground` — standard content
3. `$text-muted` — secondary info, metadata
4. `dim` or `$text-disabled` — inactive, background context


## 3. Composition and Lifecycle

### compose() — Building the Widget Tree

```python
from textual.app import ComposeResult

class MyScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():           # context manager for nesting
            yield Sidebar(id="sidebar")
            yield ContentPanel(id="content")
        yield Footer()
```

`compose()` uses yield to declare child widgets. Use `with` context managers for container
nesting. Widgets are mounted in yield order.

### render() — Simple Content Display

```python
class StatusWidget(Widget):
    def render(self) -> RenderResult:
        return f"[bold]{self.label}[/]: {self.value}"
```

Use `render()` for widgets that display content without children. Returns a string (with
Rich markup), `Text`, or any Rich renderable. Use `compose()` when the widget has child
widgets. A widget can use both — `compose()` for children, `render()` for its own content.

### Widget Lifecycle Events

Events fire in this order after mounting:

1. **`on_mount`** — DOM is ready. Safe to query children, set initial state. Most setup goes here.
2. **`on_show`** — Widget becomes visible. Fires again after `on_hide`.
3. **`on_hide`** — Widget hidden (display=none, scrolled out of view, removed).
4. **`on_ready`** — **App only.** DOM complete, first frame rendered.

```python
def on_mount(self) -> None:
    self.query_one("#sidebar-list").focus()
```

### Dynamic Content

```python
await self.mount(MyWidget(), after="#header")   # insert after element
await self.mount(MyWidget(), before=0)          # insert at start
await widget.remove()                           # remove single widget
await container.remove_children(".temporary")   # remove by CSS selector
```

`mount()` and `remove()` return awaitables. `remove_children()` accepts a CSS selector
string (default `"*"` for all children), a widget class, or an iterable of widgets.

To rebuild a widget's children from scratch, call `widget.recompose()` — this removes all
children and re-runs `compose()`.

### Anti-Patterns

- **Querying DOM in `__init__`** — Children don't exist yet. Use `on_mount`.
- **Using `compose()` when `render()` suffices** — If a widget just displays text, use `render()`.
- **Rebuilding entire DOM vs updating in place** — Use `widget.update()`, `add_class()`/`remove_class()`, or reactive attributes instead of tearing down and remounting.


## 4. Reactive State Management

Reactive attributes automatically trigger UI updates when changed.

### reactive() vs var()

```python
from textual.reactive import reactive, var

class MyWidget(Widget):
    count = reactive(0)        # triggers repaint on change, calls watchers on init
    label = reactive("Hello")  # same — repaint + init watcher
    data = var([])             # NO repaint, NO layout — just stores value + watchers
```

`reactive()` sets `repaint=True` and `init=True` by default — the widget refreshes when the
value changes and watchers fire during mount. `var()` sets both to `False` — use it for
internal state that doesn't directly affect rendering.

### Watch Methods

```python
class MyWidget(Widget):
    count = reactive(0)

    def watch_count(self, old_value: int, new_value: int) -> None:
        self.query_one("#display").update(f"Count: {new_value}")
```

Naming convention: `watch_{attribute_name}`. Three valid signatures:
- `watch_x(self, old_value, new_value)` — both values
- `watch_x(self, new_value)` — new value only
- `watch_x(self)` — no arguments

Can be sync or async. Private variant `_watch_x` also supported.

### Validate Methods

```python
def validate_count(self, value: int) -> int:
    return max(0, value)  # clamp to non-negative
```

Called before watchers. Must return the (possibly transformed) value.

### Compute Methods

```python
class MyWidget(Widget):
    first_name = reactive("")
    last_name = reactive("")

    def compute_full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"
```

Makes the reactive read-only and derived from other state. Recalculated on access.

### Mutable Collections

Python can't detect in-place mutations on lists/dicts. After mutating, signal the change:

```python
self.items.append("new")
self.mutate_reactive(MyWidget.items)   # pass the class-level descriptor
```

### Advanced Options

```python
expanded = reactive(False, toggle_class="-expanded")  # auto-toggle CSS class
mode = reactive("default", recompose=True)             # re-run compose() on change
query = reactive("", bindings=True)                    # refresh keybindings on change
```


## 5. Messages, Events, and Actions

### Event Handlers

Naming convention: `on_{snake_case_message_class}`. Textual converts the class name
automatically.

```python
def on_button_pressed(self, event: Button.Pressed) -> None:
    self.notify(f"Button {event.button.id} pressed")

def on_input_submitted(self, event: Input.Submitted) -> None:
    self.process(event.value)
```

### @on Decorator with CSS Selectors

Filter handlers by which widget emitted the message:

```python
from textual import on

@on(Button.Pressed, "#save")
def handle_save(self) -> None:
    self.save_data()

@on(Button.Pressed, "#cancel")
def handle_cancel(self) -> None:
    self.app.pop_screen()
```

Cleaner than checking `event.button.id` in a single handler.

### Event Bubbling

Messages bubble up from the emitting widget through ancestors to the Screen and App.
Control propagation:

```python
def on_button_pressed(self, event: Button.Pressed) -> None:
    event.stop()              # prevent parent from seeing this
    event.prevent_default()   # prevent default handler
```

### Custom Messages

```python
from textual.message import Message

class TaskCompleted(Message):
    ALLOW_SELECTOR_MATCH: ClassVar[set[str]] = {"control"}

    def __init__(self, task_id: str, widget: Widget) -> None:
        super().__init__()     # always call super().__init__()
        self.task_id = task_id
        self._widget = widget

    @property
    def control(self) -> Widget | None:
        return self._widget

# Emit from widget:
self.post_message(TaskCompleted("task-1", self))
```

Define `ALLOW_SELECTOR_MATCH` and `control` property to use CSS selectors with `@on`.

### Actions

Bindings trigger actions via `action_{name}` methods:

```python
class MyWidget(Widget):
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("d", "toggle_dark", "Dark mode"),
    ]

    def action_quit(self) -> None:
        self.app.exit()

    def action_toggle_dark(self) -> None:
        self.app.theme = "textual-dark" if self.app.theme != "textual-dark" else "textual-light"
```

Action namespace: `"quit"` looks up the focused widget chain. `"app.quit"` forces lookup
on the App. `"screen.my_action"` forces lookup on the Screen.


## 6. Theme System

Textual has a built-in theme system that replaces hardcoded colour values with CSS variables.

### Registering a Custom Theme

```python
from textual.theme import Theme

def create_my_theme() -> Theme:
    return Theme(
        name="my-theme",
        primary="#d98e64",
        secondary="#aa96c8",
        accent="#d98e64",
        foreground="#d4d4d4",
        background="#1e1e1e",
        surface="#252525",
        panel="#333333",
        success="#10b981",
        warning="#f59e0b",
        error="#ef4444",
        dark=True,
        variables={
            "dim": "#646464",
            "accent-muted": "#c4a882",
        },
    )

class MyApp(App):
    def __init__(self):
        super().__init__()
        self.register_theme(create_my_theme())
        self.theme = "my-theme"
```

### Auto-Generated Shades

From each base colour, Textual generates:
- 3 lighter shades: `$primary-lighten-1`, `-lighten-2`, `-lighten-3`
- 3 darker shades: `$primary-darken-1`, `-darken-2`, `-darken-3`
- Muted variants: `$primary-muted` (70% opacity blend with background)
- Text colours: `$text-primary` (guaranteed legible on `$primary-muted`)

### CSS Variables Available

| Variable | Purpose |
|----------|---------|
| `$primary` | Brand/highlight colour |
| `$secondary` | Secondary accent |
| `$accent` | Attention elements |
| `$foreground` | Default text |
| `$background` | App background |
| `$surface` | Widget/card backgrounds |
| `$panel` | Borders, dividers |
| `$success`, `$warning`, `$error` | Status colours |
| `$text-muted` | Reduced emphasis text |
| `$text-disabled` | Inactive text |
| `$text` | Contrast-optimized (black or white) |

Custom variables defined in `Theme(variables={...})` are available as `$variable-name`.

For duct-tui: define semantic variables like `agent-active`, `agent-idle`, `agent-error`
in the theme's `variables` dict so agent status colors are consistent app-wide.

### Anti-Pattern: Hardcoded Hex

Never hardcode hex values in TCSS. Use CSS variables from the theme. Rich markup in Python
is the one exception — keep those constants in a single theme module.


## 7. CSS and Styling

### External CSS Files

```python
class MyApp(App):
    CSS_PATH = "app.tcss"  # resolved relative to this Python file
```

Benefits: live editing with `textual run --dev`, separation of concerns, easier review.

### Widget DEFAULT_CSS

Keep this minimal — just structural styles the widget needs to function. Layout and
theming belong in the app-level `.tcss` file.

### Selectors

| Selector | Example | Matches |
|----------|---------|---------|
| Type | `Button` | All Button widgets (and subclasses) |
| ID | `#sidebar` | Widget with `id="sidebar"` |
| Class | `.active` | Widgets with CSS class `active` |
| Descendant | `#sidebar Button` | Button anywhere inside #sidebar |
| Child | `#sidebar > Button` | Direct Button children of #sidebar |

### Pseudo-Classes

`:focus`, `:focus-within`, `:hover`, `:disabled`, `:enabled`, `:dark`, `:light`,
`:first-child`, `:last-child`, `:even`, `:odd`

### Component Classes

Built-in widgets expose component classes for styling internal parts:

```css
OptionList > .option-list--option-highlighted { background: $surface; }
DataTable > .datatable--cursor { background: $primary-muted; }
Tree > .tree--cursor { background: $surface; text-style: bold; }
```

### CSS Transitions

```css
Button {
    transition: background 0.3s out_cubic, opacity 0.2s linear;
}
```

Animatable properties include: `offset`, `padding`, `margin`, `width`, `height`,
`min_width`, `max_width`, `min_height`, `max_height`, `color`, `background`, `opacity`,
`tint`, `text_opacity`.

Easing functions: `linear`, `in_cubic`, `out_cubic`, `in_out_cubic`, `in_elastic`,
`out_elastic`, `in_bounce`, `out_bounce`, and many more.

### Border vs Outline

- **Border** — takes space in layout. Types: `solid`, `heavy`, `double`, `round`, `dashed`,
  `tall`, `wide`, `panel`, `ascii`, `blank`, `inner`, `outer`, `thick`, `block`
- **Outline** — drawn on top, does NOT affect layout.

```css
#panel { border: round $panel; }          /* takes 1 cell per side */
#highlight { outline: heavy $primary; }   /* overlays, no space change */
```

### Grid Layout

```css
#grid-container {
    layout: grid;
    grid-size-columns: 3;
    grid-size-rows: 2;
    grid-gutter-horizontal: 1;
    grid-gutter-vertical: 1;
    grid-columns: 20 1fr 30%;   /* explicit column widths */
}

.span-two { column-span: 2; }
```

### Visibility

```css
.hidden-no-space { display: none; }         /* removed from layout */
.hidden-keep-space { visibility: hidden; }  /* invisible but space reserved */
```

Toggle with `widget.add_class("hidden-no-space")` / `widget.remove_class(...)`.


## 8. Layout

### Work Outside-In

Start with fixed elements (header, footer, sidebar), then fill remaining space:

```css
Header { dock: top; }
Footer { dock: bottom; }
#sidebar { dock: left; width: 36; }
#content { width: 1fr; }
```

### Containers

```python
from textual.containers import Horizontal, Vertical, ScrollableContainer,
    VerticalScroll, HorizontalScroll
```

| Container | Scrollable | Layout |
|-----------|-----------|--------|
| `Container` / `Vertical` | No | Vertical |
| `Horizontal` | No | Horizontal |
| `ScrollableContainer` | Both axes | Vertical |
| `VerticalScroll` | Y only | Vertical |
| `HorizontalScroll` | X only | Horizontal |

### Fraction Units

```css
.col-a { width: 1fr; }  /* 1/3 */
.col-b { width: 2fr; }  /* 2/3 */
```

### Scrolling

```css
#content { overflow-y: auto; }    /* scrollbar when needed */
#always { overflow: scroll; }     /* always show scrollbar */
```

Programmatic scrolling:
```python
widget.scroll_visible()                        # scroll parent to show this widget
container.scroll_to_widget(child, center=True) # center child in view
container.scroll_home()                        # scroll to top
container.scroll_end()                         # scroll to bottom
```

### Min/Max Constraints

```css
#sidebar { width: 36; min-width: 20; max-width: 60; }
#content { height: 1fr; min-height: 10; max-height: 80%; }
```

### Spacing

Terminal UIs feel cramped by default. Use generous padding and margin:
```css
#content { padding: 1 4; }
MarkdownH2 { margin: 2 0 1 0; }
```


## 9. Widget Reference

Always check if a built-in widget exists before writing a custom one.

### Footer — Auto-Discovering Keybindings

```python
yield Footer()
```

Automatically discovers `BINDINGS` from the focused widget and its ancestors. As focus
moves, the footer updates to show relevant keybindings. Bindings with `show=False` are
hidden. No manual rendering needed.

### Header — App Title Bar

```python
yield Header(show_clock=True)
```

Displays `App.title` and `App.sub_title`. Screen-level titles via `Screen.TITLE` and
`Screen.SUB_TITLE`.

### Tabs / Tab — Tabbed Navigation

```python
from textual.widgets import Tabs, Tab

yield Tabs(Tab("First", id="tab-0"), Tab("Second", id="tab-1"))
```

Arrow key navigation, `Tabs.TabActivated` event, dynamic add/remove, `tabs.active` property.
Tab labels accept Rich text.

### TabbedContent — Tabs + Content Panels

```python
from textual.widgets import TabbedContent, TabPane

with TabbedContent():
    with TabPane("Overview", id="overview"):
        yield OverviewWidget()
    with TabPane("Details", id="details"):
        yield DetailsWidget()
```

Combines `Tabs` with `ContentSwitcher`. `active` reactive attribute controls which pane
is visible. Preferred over manual Tabs + ContentSwitcher for most use cases.

### OptionList — Selectable Lists

```python
from textual.widgets import OptionList
from textual.widgets.option_list import Option

ol = OptionList(id="my-list", markup=True)
ol.add_option(Option("Section Header", disabled=True))
ol.add_option(Option("  Item One", id="item-1"))
ol.add_option(None)                                    # separator
ol.add_option(Option("  Item Two", id="item-2"))
```

Up/down/pgup/pgdn/home/end navigation, Enter to select, `OptionHighlighted` and
`OptionSelected` events, disabled options, separators, Rich markup labels.

### RichLog — Streaming Output

```python
from textual.widgets import RichLog

log = RichLog(max_lines=1000, auto_scroll=True, markup=True, highlight=True)
log.write("Processing started...")
log.write("[bold green]Success![/]")
log.write(some_rich_renderable)     # accepts any Rich renderable
log.clear()
```

Critical for streaming agent output. `max_lines` caps memory usage. `auto_scroll` keeps
the latest output visible. `highlight` enables syntax highlighting via `ReprHighlighter`.

Use `RichLog` over `Log` — `Log` is simpler (text-only via `write_line()`) but `RichLog`
handles Rich renderables, markup, and highlighting.

### DataTable — Configurable Table

```python
from textual.widgets import DataTable

table = DataTable(cursor_type="row")   # "cell", "row", "column", "none"
table.add_columns("Name", "Status", "Duration")
table.add_row("Task A", "✓ Complete", "2m 31s", key="task-a")
table.update_cell("task-a", "Status", "● Running")
table.sort("Name")
```

Events: `RowSelected`, `RowHighlighted`, `CellSelected`, `CellHighlighted`. Fixed rows/
columns via `fixed_rows`/`fixed_columns` constructor params.

### Tree — Hierarchical Navigation

```python
from textual.widgets import Tree

tree = Tree("Root", data=root_data)
node = tree.root.add("Child", data=child_data)
node.add("Grandchild")
tree.root.expand_all()
```

Events: `NodeSelected`, `NodeExpanded`, `NodeCollapsed`. Each node has a `data` attribute
for associated objects. Lazy loading via `on_tree_node_expanded` — populate children on
first expand.

### Input — Text Entry

```python
from textual.widgets import Input
from textual.validation import Integer

yield Input(
    placeholder="Enter a value...",
    validators=[Integer(minimum=0, maximum=100)],
    validate_on=["changed", "submitted"],
)
```

Events: `Input.Changed`, `Input.Submitted`. Types: `"text"`, `"integer"`, `"number"`.
Supports `restrict` regex, `password` mode, `max_length`, `suggester`.

### TextArea — Code Editor

```python
from textual.widgets import TextArea

yield TextArea(language="python", theme="dracula")
```

Full editor with syntax highlighting (Python, JSON, YAML, HTML, CSS, SQL, and more),
line numbers, selection, code folding.

### Collapsible — Progressive Disclosure

```python
from textual.widgets import Collapsible

with Collapsible(title="Advanced Options", collapsed=True):
    yield AdvancedForm()
```

`collapsed` reactive attribute. Events: `Collapsible.Expanded`, `Collapsible.Collapsed`.
Custom symbols via `collapsed_symbol`/`expanded_symbol`.

### Select — Dropdown

```python
from textual.widgets import Select

yield Select(
    options=[("Option A", "a"), ("Option B", "b")],
    allow_blank=True,
)
```

`Select.Changed` event. `value` reactive property. `Select.NULL` sentinel for unselected.

### SelectionList — Multi-Select

```python
from textual.widgets import SelectionList, Selection

yield SelectionList(
    Selection("Enable logging", "logging", initial_state=True),
    Selection("Verbose output", "verbose"),
)
```

### ProgressBar

```python
from textual.widgets import ProgressBar

bar = ProgressBar(total=100, show_eta=True)
bar.advance(10)          # increment by 10
bar.update(progress=50)  # set to 50
```

`total=None` for indeterminate progress.

### LoadingIndicator

```python
from textual.widgets import LoadingIndicator

yield LoadingIndicator()   # animated spinner, blocks input while visible
```

Mount when loading starts, `remove()` when done.

### Static / Label

- **Static** — generic content display, base for custom widgets. `update(content)` method.
- **Label** — specialized for text with color variants: `variant="success"`, `"error"`,
  `"warning"`, `"primary"`, `"secondary"`, `"accent"`.

### Tooltips

```python
my_widget.tooltip = "Helpful description"
# or chainable:
Label("Status").with_tooltip("Current connection status")
```

### Other Useful Widgets

- **ListView / ListItem** — Widget-based list where each item contains any widgets
- **MarkdownViewer** — Scrollable markdown with optional table of contents
- **Rule** — Horizontal separator
- **DirectoryTree** — File browser tree


## 10. Screens and Navigation

### Screen Stack

```python
self.app.push_screen(DetailScreen())         # add to stack
self.app.pop_screen()                        # remove top screen
self.app.switch_screen(OtherScreen())        # replace top screen
```

`push_screen` adds a new screen on top. `pop_screen` returns to the previous one.
`switch_screen` replaces the current screen (more efficient than pop + push).

### Modal Screens

```python
from textual.screen import ModalScreen

class ConfirmDialog(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "dismiss_modal")]

    def compose(self) -> ComposeResult:
        yield Label("Are you sure?")
        yield Button("Yes", id="yes")
        yield Button("No", id="no")

    @on(Button.Pressed, "#yes")
    def confirm(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#no")
    def cancel(self) -> None:
        self.dismiss(False)

    def action_dismiss_modal(self) -> None:
        self.dismiss(False)

# Push with callback:
self.app.push_screen(ConfirmDialog(), callback=self.on_confirm)
```

Modal screens block interaction with the screen beneath and dim it.
`ModalScreen[ResultType]` is generic — `dismiss(result)` passes the result to the callback.

### Screen-Level Titles

```python
class MyScreen(Screen):
    TITLE = "My Screen"
    SUB_TITLE = "Details"
```

These appear in the Header widget if one is present.

### Screen Modes

```python
class MyApp(App):
    MODES = {"dashboard": DashboardScreen, "settings": SettingsScreen}

    def on_mount(self):
        self.switch_mode("dashboard")
```

Each mode has its own screen stack. Useful for apps with distinct operational contexts.


## 11. Async, Workers, and Timers

### @work Decorator

```python
from textual import work

@work(thread=True)
def load_data(self) -> list:
    return expensive_blocking_operation()

@work(exclusive=True)
async def search(self, query: str) -> list:
    results = await fetch_results(query)
    self.update_results(results)
```

`thread=True` for blocking I/O. `exclusive=True` cancels previous workers in the same
group — use for search-as-you-type where only the latest query matters.

### run_worker

```python
def on_mount(self):
    self.run_worker(self._load_data, thread=True)
```

Alternative to `@work` decorator for one-off operations.

### call_from_thread

```python
@work(thread=True)
def background_task(self):
    data = expensive_io()
    self.app.call_from_thread(self.update_display, data)
```

Safe way to call UI methods from a thread worker. Required because widget methods aren't
thread-safe.

### Timers

```python
# Repeat every second
interval = self.set_interval(1.0, self.refresh_status)

# One-shot after 3 seconds
timer = self.set_timer(3.0, self.hide_notification)

# Control
interval.pause()
interval.resume()
interval.stop()
```


## 12. Command Palette and Discoverability

Ctrl+P opens the built-in command palette (enabled by default).

### Custom System Commands

```python
from textual.app import SystemCommand

class MyApp(App):
    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        yield from super().get_system_commands(screen)
        yield SystemCommand("Clear Log", "Clear all log output", self.action_clear_log)
```

### Custom Command Providers

```python
from textual.command import Provider, Hit, DiscoveryHit, Hits

class TaskProvider(Provider):
    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for task in self.app.tasks:
            if (score := matcher.match(task.name)) > 0:
                yield Hit(score, matcher.highlight(task.name),
                         self.select_task, text=task.name)

    async def discover(self) -> Hits:
        for task in self.app.tasks[:5]:
            yield DiscoveryHit(task.name, self.select_task, text=task.name)

class MyApp(App):
    COMMANDS = {TaskProvider}
```

`discover()` returns default suggestions when the palette opens with no input.
`search()` provides fuzzy-matched results as the user types.


## 13. Testing

### Pilot Mode

```python
import pytest
from my_app import MyApp

@pytest.fixture
def app():
    return MyApp()

async def test_button_click(app):
    async with app.run_test() as pilot:
        await pilot.click("#submit")
        assert app.query_one("#status").renderable == "Submitted"

async def test_keyboard_navigation(app):
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.press("tab", "tab", "enter")
        await pilot.press("j", "j", "k")   # vim navigation
```

`run_test()` returns a `Pilot` — use `press()` for key simulation, `click()` for mouse.
Default terminal size is (80, 24); override with `size=`.

### What to Test

- Widget state after user interactions (button clicks, key presses)
- Reactive attribute changes propagate correctly
- Message handling: correct messages emitted and handled
- Screen navigation: push/pop/dismiss flows
- One test file per screen or major widget


## 14. Common Anti-Patterns

**DOM teardown/rebuild for selection changes** — Mount widgets once, update via `.update()`,
`.add_class()`, `.remove_class()`. Or use OptionList which handles this internally.

**Giant APP_CSS f-strings** — Use external `.tcss` files with CSS variables from the theme.

**Manual focus tracking with strings** — Use Textual's native focus with widget-level BINDINGS.
If `j` means "navigate" in one context and "scroll" in another, those bindings belong on
different widgets, not dispatched via `if self._focus`.

**Reimplementing built-in widgets** — Check the widget gallery first. Footer, Tabs, OptionList,
MarkdownViewer, Header, ListView, DataTable all exist and are well-tested.

**Querying DOM in `__init__`** — Children don't exist yet. Move queries to `on_mount`.

**Manual `refresh()` instead of reactives** — Use `reactive()` attributes. Textual refreshes
the widget automatically when the value changes.

**Long if/elif message dispatch** — Use `@on` decorator with CSS selectors instead of checking
`event.button.id` in a chain of conditionals.

**Blocking the event loop** — Use `@work(thread=True)` for blocking I/O. Use
`@work(exclusive=True)` for cancellable operations.

**Missing `mutate_reactive()` for mutable containers** — After `self.items.append(x)`, call
`self.mutate_reactive(MyWidget.items)`. Python can't detect in-place mutations.

**Disabling animations to hide problems** — If you need `animate=False` to prevent visual
glitches, the underlying render pattern is wrong. Fix the root cause.
