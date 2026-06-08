"""ArtifactView -- markdown artifact viewer with inline mermaid diagrams.

Two rendering paths:

- No mermaid fences: single-widget rendering, chosen by the `display.fastMarkdown`
  flag. Default uses Textual's Markdown widget (clickable links, one child per
  block so hide/show scales with document size). `fast` mode uses a Static with
  a Rich Markdown renderable (constant-cost hide/show, no link clicks).

- Contains ```mermaid fences: multi-segment rendering. Each prose run becomes a
  Markdown/Static; each mermaid block becomes a textual-image Image widget with
  the diagram pre-rendered to PNG by mermaid-cli (mmdc). If mmdc isn't on PATH
  the mermaid block falls back to code-block rendering of the source.
"""

from __future__ import annotations

from pathlib import Path

from rich.markdown import Markdown as RichMarkdown
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widget import Widget
from textual.widgets import Markdown, Static

# textual-image probes the terminal for Sixel / TGP support at import time and
# the probe stops working once Textual has grabbed stdin. Import eagerly so the
# detection runs while stdio is still ours, otherwise AutoImage silently falls
# back to halfcell (pixelated) rendering.
from textual_image.widget import Image as TerminalImage

from duct_tui.mermaid import is_available as mmdc_available, render_to_png


def _split_segments(content: str) -> list[tuple[str, str]]:
    """Split markdown into ('md', text) / ('mermaid', source) segments.

    Extracts a ```mermaid block only when an opening fence (exactly ```mermaid
    after stripping) is paired with a closing ``` line. Unclosed fences stay as
    plain markdown.
    """
    segments: list[tuple[str, str]] = []
    lines = content.split("\n")
    buffer: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        if lines[i].strip().lower() == "```mermaid":
            j = i + 1
            while j < n and lines[j].strip() != "```":
                j += 1
            if j < n:
                if buffer:
                    segments.append(("md", "\n".join(buffer)))
                    buffer = []
                segments.append(("mermaid", "\n".join(lines[i + 1:j])))
                i = j + 1
                continue
        buffer.append(lines[i])
        i += 1
    if buffer:
        segments.append(("md", "\n".join(buffer)))
    return segments


class ArtifactView(VerticalScroll, can_focus=True):
    BINDINGS = [
        Binding("j", "scroll_down", "Down", show=False),
        Binding("k", "scroll_up", "Up", show=False),
    ]

    _PLACEHOLDER = "*Select an artifact to view*"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.border_title = "Artifact"
        self._notified_missing_mmdc = False

    def compose(self) -> ComposeResult:
        if getattr(self.app, "fast_markdown", False):
            yield Static(RichMarkdown(self._PLACEHOLDER), id="artifact-content")
        else:
            yield Markdown(self._PLACEHOLDER, id="artifact-content")

    def update_artifact(self, name: str, content: str) -> None:
        self.border_title = name
        segments = _split_segments(content)
        if not any(kind == "mermaid" for kind, _ in segments):
            self._render_single(content)
            return
        if not mmdc_available() and not self._notified_missing_mmdc:
            self._notified_missing_mmdc = True
            self.app.notify(
                "mermaid-cli (mmdc) not installed - diagrams shown as code. "
                "Install: npm i -g @mermaid-js/mermaid-cli",
                severity="warning",
            )
        self._render_segments(segments)

    # -- Rendering paths --

    def _render_single(self, content: str) -> None:
        existing = self.query("#artifact-content")
        if existing:
            widget = existing.first()
            if isinstance(widget, Markdown):
                widget.update(content)
            else:
                widget.update(RichMarkdown(content))
        else:
            self.remove_children()
            if getattr(self.app, "fast_markdown", False):
                self.mount(Static(RichMarkdown(content), id="artifact-content"))
            else:
                self.mount(Markdown(content, id="artifact-content"))
        self.scroll_home(animate=False)

    def _render_segments(self, segments: list[tuple[str, str]]) -> None:
        fast = getattr(self.app, "fast_markdown", False)
        self.remove_children()
        for i, (kind, body) in enumerate(segments):
            seg_id = f"seg-{i}"
            if kind == "md":
                self.mount(self._build_md_widget(body, seg_id, fast))
            else:
                placeholder_id = f"{seg_id}-ph"
                self.mount(Static(
                    RichMarkdown("*Rendering diagram...*"),
                    id=placeholder_id,
                    classes="mermaid-placeholder",
                ))
                self._render_mermaid_segment(seg_id, placeholder_id, body)
        self.scroll_home(animate=False)

    @staticmethod
    def _build_md_widget(body: str, widget_id: str, fast: bool) -> Widget:
        if fast:
            return Static(RichMarkdown(body), id=widget_id)
        return Markdown(body, id=widget_id)

    @work(thread=True, exclusive=False)
    def _render_mermaid_segment(
        self, seg_id: str, placeholder_id: str, source: str,
    ) -> None:
        path = render_to_png(source)
        self.app.call_from_thread(
            self._swap_mermaid, seg_id, placeholder_id, source, path,
        )

    def _swap_mermaid(
        self,
        seg_id: str,
        placeholder_id: str,
        source: str,
        path: Path | None,
    ) -> None:
        placeholders = self.query(f"#{placeholder_id}")
        if not placeholders:
            return
        placeholder = placeholders.first()
        replacement = self._build_mermaid_replacement(seg_id, source, path)
        self.mount(replacement, before=placeholder)
        placeholder.remove()

    def _build_mermaid_replacement(
        self, seg_id: str, source: str, path: Path | None,
    ) -> Widget:
        if path is not None:
            return TerminalImage(str(path), id=seg_id, classes="mermaid-image")
        fallback = f"```mermaid\n{source}\n```"
        return self._build_md_widget(
            fallback, seg_id, getattr(self.app, "fast_markdown", False),
        )
