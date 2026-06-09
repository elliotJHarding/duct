"""Orchestrator launching and stream formatting.

Shared logic used by both the CLI (orchestrate_cmd) and the TUI.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from duct import paths
from duct.config import load_config
from duct.prompts import load_prompt

ALLOWED_TOOLS = ["Read", "Glob", "Grep", "Write", "Edit", "Bash", "Agent"]


def build_prompt(ticket_key: str | None = None, fork_model: str = "sonnet") -> str:
    """Build the -p prompt for the orchestrator session.

    ``fork_model`` is the model alias the prompt tells the read-only per-ticket
    fan-out forks to spawn with; the parent keeps its inherited (Opus) model.
    """
    ticket_focus = f"\nFocus this session on ticket {ticket_key}." if ticket_key else ""
    return load_prompt("orchestrator", ticket_focus=ticket_focus, fork_model=fork_model)


_TOOL_STYLE = "grey50"
_MAX_DETAIL = 80


def _shorten_path(raw: str, root: Path | None) -> str:
    """Shorten a path for log display.

    Prefer workspace-relative, then home-relative, else absolute. If still
    longer than _MAX_DETAIL, truncate the middle and keep the tail — the
    filename is usually the most informative part.
    """
    if not raw:
        return raw
    shown = raw
    try:
        p = Path(raw)
        if root is not None:
            try:
                shown = str(p.relative_to(root))
            except ValueError:
                pass
        if shown == raw:
            try:
                shown = "~/" + str(p.relative_to(Path.home()))
            except ValueError:
                pass
    except Exception:
        shown = raw
    if len(shown) > _MAX_DETAIL:
        shown = "…" + shown[-(_MAX_DETAIL - 1):]
    return shown


def _format_tool_use(content_block: dict, root: Path | None = None) -> str | None:
    """Format a tool_use content block into a concise one-liner."""
    name = content_block.get("name", "")
    inp = content_block.get("input", {})

    detail = ""
    if name in ("Read", "Write", "Edit"):
        detail = _shorten_path(inp.get("file_path", ""), root)
    elif name == "Glob":
        detail = inp.get("pattern", "")
    elif name == "Grep":
        pattern = inp.get("pattern", "")
        path = _shorten_path(inp.get("path", ""), root)
        detail = f"{pattern}" + (f" in {path}" if path else "")
    elif name == "Bash":
        cmd = inp.get("command", "")
        detail = cmd[:_MAX_DETAIL] + ("…" if len(cmd) > _MAX_DETAIL else "")
    else:
        for v in inp.values():
            if isinstance(v, str):
                detail = v[:_MAX_DETAIL]
                break

    return f"[{_TOOL_STYLE}]    ↳ {name}  {detail}[/{_TOOL_STYLE}]"


def format_stream_event(line: str, root: Path | None = None) -> str | None:
    """Parse one NDJSON line and return a Rich-markup formatted string, or None to skip."""
    try:
        event = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None

    etype = event.get("type")

    if etype == "system" and event.get("subtype") == "init":
        model = event.get("model", "unknown")
        return f"[dim]  [init] model={model}[/dim]"

    if etype == "assistant":
        contents = event.get("message", {}).get("content", [])
        parts: list[str] = []
        for block in contents:
            btype = block.get("type")
            if btype == "tool_use":
                formatted = _format_tool_use(block, root)
                if formatted:
                    parts.append(formatted)
            elif btype == "text":
                text = block.get("text", "").strip()
                if text:
                    if len(text) > 200:
                        text = text[:200] + "..."
                    parts.append(text)
        if parts:
            return "\n".join(parts)

    if etype == "result":
        duration = event.get("duration_seconds", 0)
        cost = event.get("cost_usd", 0)
        turns = event.get("num_turns", 0)
        return f"[bold]  [done] {turns} turns, {duration:.1f}s, ${cost:.2f}[/bold]"

    return None


def launch(
    root: Path,
    ticket_key: str | None = None,
) -> subprocess.Popen:
    """Launch an orchestrator session and return the Popen handle.

    The process is started with ``--verbose --output-format stream-json``
    and ``stdout=PIPE`` so the caller can iterate NDJSON lines.
    """
    cfg = load_config(root)

    claude_bin = shutil.which("claude")
    if not claude_bin:
        raise FileNotFoundError("'claude' CLI not found on PATH")

    # Ensure sandbox config at workspace root
    if cfg.sandbox.enabled:
        from duct.sandbox import write_settings

        write_settings(root, cfg.sandbox)

    prompt = build_prompt(ticket_key, cfg.orchestrator.fork_model)

    cmd = [
        claude_bin,
        "--add-dir", str(root),
        "-p", prompt,
        "--allowedTools", ",".join(ALLOWED_TOOLS),
        "--verbose", "--output-format", "stream-json",
    ]

    use_skip_permissions = cfg.sandbox.skip_permissions
    if use_skip_permissions:
        if not cfg.sandbox.enabled:
            raise ValueError(
                "--skip-permissions requires sandbox to be enabled. "
                "Set sandbox.enabled in config.yaml."
            )
        cmd.append("--dangerously-skip-permissions")

    return subprocess.Popen(
        cmd,
        cwd=str(root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _tool_use_summary(block: dict) -> str:
    """Condense a tool_use block to a single-line markdown bullet."""
    name = block.get("name", "")
    inp = block.get("input", {})

    if name in ("Read", "Write", "Edit"):
        detail = inp.get("file_path", "")
    elif name == "Glob":
        detail = inp.get("pattern", "")
    elif name == "Grep":
        pattern = inp.get("pattern", "")
        path = inp.get("path", "")
        detail = f"`{pattern}`" + (f" in `{path}`" if path else "")
    elif name == "Bash":
        cmd = inp.get("command", "")
        detail = f"`{cmd[:120]}{'...' if len(cmd) > 120 else ''}`"
    else:
        detail = ""
        for v in inp.values():
            if isinstance(v, str):
                detail = v[:120]
                break

    if name in ("Read", "Write", "Edit", "Glob"):
        return f"**{name}** `{detail}`" if detail else f"**{name}**"
    return f"**{name}** {detail}".rstrip()


def _render_run_markdown(
    timestamp: str,
    ticket_key: str | None,
    events: list[dict],
    returncode: int,
) -> str:
    """Build the markdown summary for one orchestrator run."""
    timeline: list[str] = []
    texts: list[str] = []
    result_event: dict | None = None
    model: str | None = None

    for event in events:
        etype = event.get("type")
        if etype == "system" and event.get("subtype") == "init":
            model = event.get("model")
        elif etype == "assistant":
            for block in event.get("message", {}).get("content", []):
                btype = block.get("type")
                if btype == "tool_use":
                    timeline.append(f"- {_tool_use_summary(block)}")
                elif btype == "text":
                    text = block.get("text", "").strip()
                    if text:
                        texts.append(text)
                        timeline.append(f"- {text}")
        elif etype == "result":
            result_event = event

    turns = (result_event or {}).get("num_turns", 0)
    duration = (result_event or {}).get("duration_seconds", 0)
    cost = (result_event or {}).get("cost_usd", 0)

    frontmatter_lines = [
        "---",
        f"timestamp: {timestamp}",
    ]
    if ticket_key:
        frontmatter_lines.append(f"ticket: {ticket_key}")
    if model:
        frontmatter_lines.append(f"model: {model}")
    frontmatter_lines.extend([
        f"turns: {turns}",
        f"duration_seconds: {duration}",
        f"cost_usd: {cost}",
        f"exit_code: {returncode}",
        "---",
        "",
    ])

    # Human-friendly heading from the timestamp
    display_ts = timestamp.replace("T", " ").replace("-", ":", 2).replace("-", " ", 1)
    body = [f"# Orchestrator run — {display_ts}", ""]

    conclusion = texts[-1] if texts else ""
    body.append("## Conclusion")
    if conclusion:
        for line in conclusion.splitlines():
            body.append(f"> {line}" if line else ">")
    else:
        body.append("> _(no final output)_")
    body.append("")

    body.append("## Timeline")
    if timeline:
        body.extend(timeline)
    else:
        body.append("- _(no recorded activity)_")
    body.append("")

    return "\n".join(frontmatter_lines + body)


class RunRecorder:
    """Accumulate orchestrator stream events and persist a markdown summary.

    Usage::

        recorder = RunRecorder(root, ticket_key)
        for raw_line in proc.stdout:
            recorder.record(raw_line)
        proc.wait()
        path = recorder.finalize(proc.returncode)
    """

    def __init__(self, root: Path, ticket_key: str | None = None) -> None:
        self.root = root
        self.ticket_key = ticket_key
        self.timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        self.runs_dir = paths.runs_dir(root)
        self.path = self.runs_dir / f"{self.timestamp}.md"
        self.events: list[dict] = []

    def record(self, raw_line: str) -> None:
        try:
            event = json.loads(raw_line)
        except (json.JSONDecodeError, ValueError):
            return
        if isinstance(event, dict):
            self.events.append(event)

    def last_assistant_text(self) -> str:
        """Return the most recent assistant text block (for the TUI conductor)."""
        for event in reversed(self.events):
            if event.get("type") != "assistant":
                continue
            for block in reversed(event.get("message", {}).get("content", [])):
                if block.get("type") == "text":
                    text = block.get("text", "").strip()
                    if text:
                        return text
        return ""

    def finalize(self, returncode: int) -> Path:
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        content = _render_run_markdown(
            self.timestamp, self.ticket_key, self.events, returncode,
        )
        self.path.write_text(content)
        return self.path


_TIMESTAMP_FMT = "%Y-%m-%dT%H-%M-%S"


@dataclass(frozen=True)
class RunSummary:
    """Lightweight handle to a persisted orchestrator run."""

    path: Path
    timestamp: datetime
    ticket_key: str | None
    exit_code: int | None


def _parse_run_frontmatter(path: Path) -> dict[str, str] | None:
    """Read just the YAML frontmatter from a run markdown file.

    Frontmatter is flat scalars (see _render_run_markdown), so a manual
    line parser avoids pulling in a YAML dependency. Returns None if the
    file has no recognisable frontmatter.
    """
    try:
        with path.open("r", encoding="utf-8") as fp:
            first = fp.readline()
            if first.strip() != "---":
                return None
            fields: dict[str, str] = {}
            for line in fp:
                stripped = line.strip()
                if stripped == "---":
                    return fields
                if ":" not in stripped:
                    continue
                key, _, value = stripped.partition(":")
                fields[key.strip()] = value.strip()
    except OSError:
        return None
    return None


def list_runs(
    root: Path,
    *,
    ticket_key: str | None = None,
    limit: int | None = None,
) -> list[RunSummary]:
    """Return persisted orchestrator runs, newest first.

    ``ticket_key=None`` (default) returns only workspace-level runs —
    those without a ``ticket:`` field in their frontmatter. Pass a
    string to filter to runs scoped to that ticket.
    """
    runs_dir = paths.runs_dir(root)
    if not runs_dir.is_dir():
        return []

    summaries: list[RunSummary] = []
    for path in runs_dir.glob("*.md"):
        fields = _parse_run_frontmatter(path)
        if not fields:
            continue
        ts_raw = fields.get("timestamp")
        if not ts_raw:
            continue
        try:
            timestamp = datetime.strptime(ts_raw, _TIMESTAMP_FMT)
        except ValueError:
            continue
        run_ticket = fields.get("ticket") or None
        if ticket_key is None:
            if run_ticket is not None:
                continue
        elif run_ticket != ticket_key:
            continue
        exit_raw = fields.get("exit_code")
        try:
            exit_code = int(exit_raw) if exit_raw is not None else None
        except ValueError:
            exit_code = None
        summaries.append(
            RunSummary(
                path=path,
                timestamp=timestamp,
                ticket_key=run_ticket,
                exit_code=exit_code,
            ),
        )

    summaries.sort(key=lambda s: s.timestamp, reverse=True)
    if limit is not None:
        summaries = summaries[:limit]
    return summaries


def read_run_body(path: Path) -> str:
    """Return the markdown body of a run file, with frontmatter stripped."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    if not text.startswith("---"):
        return text
    # Strip everything up to and including the closing '---' line.
    lines = text.splitlines()
    closing = -1
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            closing = idx
            break
    if closing < 0:
        return text
    return "\n".join(lines[closing + 1 :]).lstrip("\n")
