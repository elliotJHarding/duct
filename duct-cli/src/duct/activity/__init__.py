"""Activity log subsystem.

Aggregates what-you-did-today across Jira, GitHub, git, Claude transcripts,
and Outlook calendar into an append-only JSONL stream stored under
``{workspace}/.activity/YYYY-MM-DD.jsonl`` per UTC date.

The timeline is **ticket-aware**: events link to a workspace ticket when a key
can be inferred from commit messages, branch names, PR titles, subjects, or
working directories, but un-ticketed events remain on the log.
"""

from duct.activity.base import ActivityProvider, ProviderResult
from duct.activity.coordinator import ActivityCoordinator

__all__ = ["ActivityProvider", "ProviderResult", "ActivityCoordinator"]
