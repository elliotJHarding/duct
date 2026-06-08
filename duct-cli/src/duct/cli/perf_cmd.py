"""`duct perf` — surface recent timings from the perf log.

Reads `~/.duct/perf.jsonl` and groups entries by span name, showing
count / p50 / p95 / max so the user can spot regressions in the
startup, preview, dock, and subprocess hot paths.
"""

from __future__ import annotations

import click

from duct import perf
from duct.cli.output import output, section


@click.command()
@click.option(
    "--limit",
    type=int,
    default=2000,
    show_default=True,
    help="Most recent N rows to summarise.",
)
@click.option(
    "--name",
    type=str,
    default=None,
    help="Filter to a single span name (e.g. tui.load_initial, wezterm.list).",
)
@click.option(
    "--tail",
    is_flag=True,
    help="Print the most recent rows verbatim instead of summary stats.",
)
def perf_cmd(limit: int, name: str | None, tail: bool) -> None:
    """Show timing statistics from ~/.duct/perf.jsonl.

    Spans are grouped by name and sorted by total time. Use this to confirm
    that the duct-tui startup and session preview/dock hot paths are within
    their expected budgets.
    """
    entries = perf.recent(name=name, limit=limit)
    if not entries:
        output("[yellow]No perf entries recorded yet.[/yellow]")
        output("[dim]Run duct-tui or any duct command to populate ~/.duct/perf.jsonl.[/dim]")
        return

    if tail:
        section(f"Last {len(entries)} entries (newest first)")
        for e in entries:
            meta = e.get("meta", {})
            meta_str = " ".join(f"{k}={v}" for k, v in meta.items()) if meta else ""
            output(f"  {e['name']:<30} {e['ms']:>8.1f} ms  {meta_str}")
        return

    summary = perf.summarise(entries)
    section(f"Span timings (over last {len(entries)} entries, all values in ms)")
    output(
        f"  {'span':<30} {'count':>6}  {'p50':>10}  {'p95':>10}  "
        f"{'max':>10}  {'total':>12}"
    )
    output(f"  {'-' * 30} {'-' * 6}  {'-' * 10}  {'-' * 10}  {'-' * 10}  {'-' * 12}")
    for s in summary:
        output(
            f"  {s['name']:<30} {s['count']:>6}  "
            f"{s['p50']:>10.1f}  {s['p95']:>10.1f}  "
            f"{s['max']:>10.1f}  {s['sum']:>12.1f}"
        )
