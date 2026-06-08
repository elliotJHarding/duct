"""duct activity — aggregate a unified activity log across sources."""

from __future__ import annotations

import json
import re
import sys
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import click

from duct.activity.base import ActivityProvider
from duct.activity.coordinator import ActivityCoordinator
from duct.activity.store import activity_dir, iter_events, load_state
from duct.cli.output import (
    Col,
    debug,
    error,
    get_json_mode,
    output,
    spinner,
    success,
    table,
    update_spinner,
    warn,
)
from duct.cli.resolve import resolve_root
from duct.config import AuthError, ConfigError, gh_token, jira_email, jira_token, load_config
from duct.exceptions import SyncError

_DURATION_RE = re.compile(r"^(\d+)\s*([smhdw])$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Time parsing
# ---------------------------------------------------------------------------


def _parse_time(value: str | None, *, default: datetime) -> datetime:
    """Parse a CLI time string. Accepts:
      - ``now``
      - ISO 8601 date or datetime (``2026-04-20`` or ``2026-04-20T09:00``)
      - relative durations ago: ``2h``, ``1d``, ``30m``, ``1w``
      - named days: ``today``, ``yesterday``
    """
    if value is None or value == "":
        return default
    val = value.strip().lower()
    now = datetime.now(timezone.utc).replace(microsecond=0)

    if val == "now":
        return now
    if val == "today":
        return now.replace(hour=0, minute=0, second=0)
    if val == "yesterday":
        return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0)

    m = _DURATION_RE.match(val)
    if m:
        amount = int(m.group(1))
        unit = m.group(2).lower()
        delta = {
            "s": timedelta(seconds=amount),
            "m": timedelta(minutes=amount),
            "h": timedelta(hours=amount),
            "d": timedelta(days=amount),
            "w": timedelta(weeks=amount),
        }[unit]
        return now - delta

    # ISO 8601 fallbacks.
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise click.BadParameter(
            f"couldn't parse {value!r} — use ISO date, `2h`/`1d` durations, "
            "or `now`/`today`/`yesterday`"
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Provider build-up (mirrors sync_cmd._build_all_sources)
# ---------------------------------------------------------------------------


def _build_providers(cfg, requested: list[str] | None) -> tuple[list[ActivityProvider], list[tuple[str, str]]]:
    """Instantiate enabled providers; return (providers, skipped)."""
    enabled = set(requested or cfg.activity.providers_enabled)
    providers: list[ActivityProvider] = []
    skipped: list[tuple[str, str]] = []

    if "jira" in enabled:
        try:
            from duct.activity.providers.jira import JiraActivityProvider

            providers.append(
                JiraActivityProvider(
                    domain=cfg.jira_domain,
                    email=jira_email(),
                    token=jira_token(),
                )
            )
        except AuthError as exc:
            skipped.append(("jira", str(exc)))

    if "github" in enabled:
        try:
            from duct.activity.providers.github import GitHubActivityProvider
            from duct.config import github_username

            providers.append(
                GitHubActivityProvider(token=gh_token(), username=github_username())
            )
        except AuthError as exc:
            skipped.append(("github", str(exc)))

    if "git" in enabled:
        from duct.activity.providers.git import GitActivityProvider

        providers.append(GitActivityProvider())

    if "claude" in enabled:
        from duct.activity.providers.claude import ClaudeActivityProvider

        providers.append(ClaudeActivityProvider())

    if "outlook" in enabled:
        from duct.activity.providers.outlook import OutlookActivityProvider

        providers.append(OutlookActivityProvider())

    if "outlook_pdf" in enabled:
        if cfg.activity.outlook_pdf_path:
            from duct.activity.providers.outlook_pdf import OutlookPdfActivityProvider

            providers.append(OutlookPdfActivityProvider(pdf_path=cfg.activity.outlook_pdf_path))
        else:
            skipped.append(
                ("outlook_pdf", "activity.outlookPdfPath is not set in config.yaml")
            )

    return providers, skipped


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@click.group()
@click.pass_context
def activity(ctx: click.Context) -> None:
    """Aggregate an activity log across Jira, GitHub, git, Claude, and Outlook."""
    ctx.ensure_object(dict)


@activity.command("gather")
@click.option("--since", "since_str", default=None, help="Start of window (default: last run - 1h).")
@click.option("--until", "until_str", default=None, help="End of window (default: now).")
@click.option(
    "--provider",
    "providers_csv",
    default=None,
    help="Comma-separated provider list (default: all enabled).",
)
@click.pass_context
def gather(
    ctx: click.Context,
    since_str: str | None,
    until_str: str | None,
    providers_csv: str | None,
) -> None:
    """Fetch events from each source and append to the JSONL store."""
    try:
        root = resolve_root(ctx)
        cfg = load_config(root)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    requested = [p.strip() for p in providers_csv.split(",")] if providers_csv else None
    providers, skipped = _build_providers(cfg, requested)
    for name, reason in skipped:
        warn(f"{name}: skipped ({reason})")

    if not providers:
        error("No activity providers available.")
        ctx.exit(1)
        return

    coordinator = ActivityCoordinator(root, cfg)

    until = _parse_time(until_str, default=datetime.now(timezone.utc).replace(microsecond=0))
    if since_str:
        since = _parse_time(since_str, default=until - timedelta(hours=24))
    else:
        since = coordinator.default_since([p.name for p in providers])

    debug(f"activity gather: since={since.isoformat()} until={until.isoformat()}")

    with spinner("Gathering activity...") as status:
        def on_start(name: str) -> None:
            update_spinner(status, f"Gathering {name}...")

        results = coordinator.gather(providers, since, until, on_start=on_start)

    for r in results:
        if r.errors:
            warn(
                f"{r.name}: fetched {r.events_fetched}, "
                f"{r.events_new} new, errors: {'; '.join(r.errors)}"
            )
        else:
            success(
                f"{r.name}: fetched {r.events_fetched}, "
                f"{r.events_new} new in {r.duration_seconds:.1f}s"
            )


@activity.command("log")
@click.option("--since", "since_str", default="1d", help="Start of window (default: 1d).")
@click.option("--until", "until_str", default="now", help="End of window (default: now).")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["markdown", "jsonl", "json"]),
    default="markdown",
    help="Output format.",
)
@click.option("--ticket", "ticket", default=None, help="Filter to a single ticket key.")
@click.option(
    "--source",
    "sources_csv",
    default=None,
    help="Comma-separated source filter (e.g. jira,git).",
)
@click.pass_context
def log_cmd(
    ctx: click.Context,
    since_str: str,
    until_str: str,
    fmt: str,
    ticket: str | None,
    sources_csv: str | None,
) -> None:
    """Render the stored activity log for a window."""
    try:
        root = resolve_root(ctx)
        _ = load_config(root)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    now = datetime.now(timezone.utc).replace(microsecond=0)
    until = _parse_time(until_str, default=now)
    since = _parse_time(since_str, default=now - timedelta(days=1))

    sources = {s.strip() for s in sources_csv.split(",")} if sources_csv else None

    events = []
    for e in iter_events(root, since, until):
        if ticket and e.ticket_key != ticket:
            continue
        if sources and e.source not in sources:
            continue
        events.append(e)

    if fmt == "jsonl":
        for e in events:
            sys.stdout.write(json.dumps(asdict(e), sort_keys=True) + "\n")
        return

    if fmt == "json":
        sys.stdout.write(json.dumps([asdict(e) for e in events], sort_keys=True, indent=2) + "\n")
        return

    _render_markdown(events, since, until)


def _render_markdown(events, since: datetime, until: datetime) -> None:
    """Group events by UTC date → ticket (untagged last), emit markdown."""
    if get_json_mode():
        # Respect global --json flag: mirror --format jsonl.
        for e in events:
            sys.stdout.write(json.dumps(asdict(e), sort_keys=True) + "\n")
        return

    from collections import defaultdict

    if not events:
        output(
            f"No activity between {since.strftime('%Y-%m-%d %H:%M')} "
            f"and {until.strftime('%Y-%m-%d %H:%M')} UTC."
        )
        return

    by_day: dict[str, list] = defaultdict(list)
    for e in events:
        day = e.timestamp[:10] if e.timestamp else "unknown"
        by_day[day].append(e)

    lines: list[str] = []
    lines.append(
        f"# Activity {since.strftime('%Y-%m-%d %H:%M')}"
        f" → {until.strftime('%Y-%m-%d %H:%M')} UTC"
    )
    lines.append("")

    for day in sorted(by_day.keys()):
        lines.append(f"## {day}")
        lines.append("")
        by_ticket: dict[str, list] = defaultdict(list)
        for e in by_day[day]:
            by_ticket[e.ticket_key or "(untagged)"].append(e)
        ordered_tickets = sorted(
            by_ticket.keys(), key=lambda k: (k == "(untagged)", k)
        )
        for tk in ordered_tickets:
            lines.append(f"### {tk}")
            lines.append("")
            for e in sorted(by_ticket[tk], key=lambda ev: ev.timestamp):
                time_part = e.timestamp[11:16] if len(e.timestamp) >= 16 else e.timestamp
                duration_part = ""
                if e.duration_seconds:
                    duration_part = f" _({_human_duration(e.duration_seconds)})_"
                url_part = f" [↗]({e.url})" if e.url else ""
                lines.append(
                    f"- **{time_part}** `{e.source}/{e.event_type}` "
                    f"{e.summary}{duration_part}{url_part}"
                )
            lines.append("")

    output("\n".join(lines))


def _human_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    hours = seconds / 3600
    return f"{hours:.1f}h"


@activity.command("providers")
@click.pass_context
def providers_cmd(ctx: click.Context) -> None:
    """Show providers, per-provider last-run, and event counts."""
    try:
        root = resolve_root(ctx)
        cfg = load_config(root)
    except ConfigError as exc:
        error(str(exc))
        ctx.exit(1)
        return

    providers, skipped = _build_providers(cfg, None)
    state = load_state(root)

    counts = _count_events_by_source(root)

    columns: list[str | Col] = [
        "Provider",
        "Enabled",
        "Last Run",
        Col("Event Count", justify="right"),
    ]
    rows = []
    for provider in providers:
        rows.append(
            [
                provider.name,
                "yes",
                state.get(provider.name, "never"),
                str(counts.get(provider.name, 0)),
            ]
        )
    for name, reason in skipped:
        rows.append([name, f"no ({reason})", state.get(name, "never"), str(counts.get(name, 0))])

    table("Activity Providers", columns, rows)


def _count_events_by_source(root: Path) -> dict[str, int]:
    """Scan every ``.activity/*.jsonl`` and return source -> event count."""
    counts: dict[str, int] = {}
    adir = activity_dir(root)
    if not adir.is_dir():
        return counts
    for path in adir.glob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            source = data.get("source", "")
            if source:
                counts[source] = counts.get(source, 0) + 1
    return counts
