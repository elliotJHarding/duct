"""PR markdown parser and status derivation."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from duct.markdown import parse_frontmatter
from duct.models import PRComment, PullRequest, Reviewer

_HEADING_RE = re.compile(r"^## #(\d+)\s*-\s*(.+?)(?:\s*\(DRAFT\))?\s*$")
# Field names can be multi-word (e.g. "Requested Reviewers").
_FIELD_RE = re.compile(r"^- \*\*([\w ]+)\*\*:\s*(.+)$")
_REVIEWER_RE = re.compile(r"^- @(\S+):\s*(.+)$")
_COMMENT_HEADER_RE = re.compile(
    r"^> \*\*@(\S+)\*\* on `([^`]+)`\s*\((\S+)\)\s*$"
)


def parse_pull_requests_md(content: str) -> list[PullRequest]:
    """Parse a PULL_REQUESTS.md file into PullRequest model instances."""
    _meta, body = parse_frontmatter(content)
    prs: list[PullRequest] = []

    # Split into per-PR sections on ## headings
    sections: list[tuple[re.Match, list[str]]] = []
    current_match: re.Match | None = None
    current_lines: list[str] = []

    for line in body.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            if current_match is not None:
                sections.append((current_match, current_lines))
            current_match = m
            current_lines = []
        else:
            current_lines.append(line)

    if current_match is not None:
        sections.append((current_match, current_lines))

    for heading, lines in sections:
        number = int(heading.group(1))
        raw_title = heading.group(2).strip()
        is_draft = heading.group(0).rstrip().endswith("(DRAFT)")

        fields: dict[str, str] = {}
        url = ""
        reviewers: list[Reviewer] = []
        comments: list[PRComment] = []
        subsection = "fields"

        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if stripped == "### Reviewers":
                subsection = "reviewers"
                i += 1
                continue
            if stripped == "### Outstanding Comments":
                subsection = "comments"
                i += 1
                continue

            if subsection == "fields":
                fm = _FIELD_RE.match(stripped)
                if fm:
                    fields[fm.group(1).strip().lower()] = fm.group(2)
                elif stripped.startswith("- [View on GitHub]("):
                    url = stripped.split("(", 1)[1].rstrip(")")

            elif subsection == "reviewers":
                rm = _REVIEWER_RE.match(stripped)
                if rm:
                    reviewers.append(Reviewer(login=rm.group(1), state=rm.group(2)))

            elif subsection == "comments":
                cm = _COMMENT_HEADER_RE.match(stripped)
                if cm:
                    author = cm.group(1)
                    loc = cm.group(2)
                    created = cm.group(3)
                    path = loc
                    comment_line: int | None = None
                    if ":" in loc:
                        path, _, line_str = loc.rpartition(":")
                        try:
                            comment_line = int(line_str)
                        except ValueError:
                            path = loc

                    # Collect body lines (subsequent > lines)
                    body_lines: list[str] = []
                    i += 1
                    while i < len(lines) and lines[i].startswith("> "):
                        body_lines.append(lines[i][2:])
                        i += 1
                    comments.append(PRComment(
                        author=author,
                        created_at=created,
                        body="\n".join(body_lines),
                        path=path,
                        line=comment_line,
                    ))
                    continue

            i += 1

        # Strip leading @ from author
        author = fields.get("author", "")
        if author.startswith("@"):
            author = author[1:]

        # Parse requested reviewers from comma-separated "@alice, @bob" field.
        requested_reviewers = _parse_at_list(fields.get("requested reviewers", ""))
        # Teams are stored the same way ("@org/team, ...").
        requested_teams = _parse_at_list(fields.get("requested teams", ""))
        needs_my_review = fields.get("needs review", "").strip().lower() == "true"

        prs.append(PullRequest(
            number=number,
            title=raw_title,
            repo=fields.get("repo", ""),
            branch=fields.get("branch", ""),
            state=fields.get("state", "open"),
            author=author,
            is_draft=is_draft,
            review_status=fields.get("review", "pending"),
            ci_status=fields.get("ci", ""),
            url=url,
            created_at=fields.get("created", ""),
            updated_at=fields.get("updated", ""),
            reviewers=reviewers,
            comments=comments,
            requested_reviewers=requested_reviewers,
            mergeable=fields.get("mergeable", "UNKNOWN"),
            author_avatar_url=fields.get("author avatar") or None,
            needs_my_review=needs_my_review,
            requested_teams=requested_teams,
        ))

    return prs


def _parse_at_list(raw: str) -> list[str]:
    """Parse a comma-separated "@a, @b" field into a list of bare names."""
    items: list[str] = []
    for entry in raw.split(","):
        entry = entry.strip().lstrip("@")
        if entry:
            items.append(entry)
    return items


def derive_status_label(pr: PullRequest) -> str:
    """Derive a single at-a-glance status label for a PR."""
    if pr.state == "merged":
        return "merged"
    if pr.state == "closed":
        return "closed"
    if pr.is_draft:
        return "draft"
    if pr.review_status == "CHANGES_REQUESTED":
        return "changes requested"
    if any(c.path for c in pr.comments):
        return "has comments"
    if pr.review_status == "APPROVED":
        return "approved"
    if pr.review_status == "pending" and pr.reviewers:
        return "in review"
    return "open"


def pr_action_reasons(pr: PullRequest) -> list[str]:
    """Return reasons this PR needs action from its author, in priority order.

    Empty list means no author action required. Merged and closed PRs are
    always considered done (no reasons).
    """
    if pr.state in ("merged", "closed"):
        return []

    reasons: list[str] = []
    if pr.mergeable == "CONFLICTING":
        reasons.append("merge conflicts")
    if pr.review_status == "CHANGES_REQUESTED":
        reasons.append("changes requested")
    if pr.ci_status in ("failing", "failure"):
        reasons.append("CI failing")
    unresolved = sum(1 for c in pr.comments if c.path)
    if unresolved:
        reasons.append(f"unresolved comments ({unresolved})")
    if pr.is_draft and pr.state == "open":
        reasons.append("draft")
    return reasons


def categorize_my_pr(
    pr: PullRequest,
) -> Literal["needs_action", "waiting_for_review", "done"]:
    """Bucket a PR authored by the current user for the My PRs panel."""
    if pr.state in ("merged", "closed"):
        return "done"
    if pr_action_reasons(pr):
        return "needs_action"
    return "waiting_for_review"


_STATUS_STYLES: dict[str, str] = {
    "merged": "[magenta]merged[/magenta]",
    "closed": "[dim]closed[/dim]",
    "draft": "[dim]draft[/dim]",
    "changes requested": "[yellow]changes requested[/yellow]",
    "has comments": "[yellow]has comments[/yellow]",
    "approved": "[green]approved[/green]",
    "in review": "[blue]in review[/blue]",
}


def style_status_label(label: str) -> str:
    """Wrap a status label in Rich markup."""
    return _STATUS_STYLES.get(label, label)


def load_ticket_prs(ticket_dir: Path) -> list[PullRequest]:
    """Read PULL_REQUESTS.md from a ticket dir and return parsed PRs."""
    pr_md = ticket_dir / "orchestrator" / "PULL_REQUESTS.md"
    if not pr_md.exists():
        return []
    return parse_pull_requests_md(pr_md.read_text(encoding="utf-8"))
