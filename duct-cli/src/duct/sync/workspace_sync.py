"""Local workspace state sync — git status for each ticket's repos."""

from __future__ import annotations

import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from duct.markdown import atomic_write, generate_frontmatter
from duct.models import SyncResult
from duct.sandbox import load_settings_template, merge_settings_template
from duct.workspace import enumerate_ticket_dirs, find_repo_dirs, orchestrator_dir


class WorkspaceSync:
    name = "workspace"

    def sync(self, root: Path) -> SyncResult:
        start = time.time()
        errors: list[str] = []
        synced = 0

        template = load_settings_template(root)

        for key, ticket_dir in enumerate_ticket_dirs(root):
            try:
                repos = self._find_repos(ticket_dir)
                if repos:
                    self._write_workspace_md(repos, ticket_dir)
                if template is not None:
                    merge_settings_template(ticket_dir, template)
                if repos or template is not None:
                    synced += 1
            except Exception as exc:
                errors.append(f"{key}: {exc}")

        return SyncResult(
            source=self.name,
            tickets_synced=synced,
            duration_seconds=time.time() - start,
            errors=errors,
        )

    def _find_repos(self, ticket_dir: Path) -> list[dict]:
        """Find git repos inside a ticket dir (siblings of orchestrator/)."""
        return [self._repo_info(repo) for repo in find_repo_dirs(ticket_dir)]

    def _repo_info(self, repo_path: Path) -> dict:
        """Extract git branch, status, and recent commits."""
        info = {"name": repo_path.name, "path": str(repo_path)}

        try:
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=repo_path, capture_output=True, text=True, timeout=5,
            )
            info["branch"] = result.stdout.strip() if result.returncode == 0 else "unknown"
        except Exception:
            info["branch"] = "unknown"

        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=repo_path, capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
                info["dirty"] = len(lines) > 0
                info["changes"] = len(lines)
            else:
                info["dirty"] = False
                info["changes"] = 0
        except Exception:
            info["dirty"] = False
            info["changes"] = 0

        try:
            result = subprocess.run(
                ["git", "log", "--oneline", "-3"],
                cwd=repo_path, capture_output=True, text=True, timeout=5,
            )
            info["recent_commits"] = (
                result.stdout.strip().splitlines() if result.returncode == 0 else []
            )
        except Exception:
            info["recent_commits"] = []

        return info

    def _write_workspace_md(self, repos: list[dict], ticket_dir: Path) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        parts: list[str] = []

        parts.append(generate_frontmatter(source="sync", synced_at=now))
        parts.append("")
        parts.append("# Workspace")
        parts.append("")
        parts.append(f"- **Path**: {ticket_dir}")
        parts.append("")

        parts.append("## Repos")
        parts.append("")

        for repo in repos:
            status = "dirty" if repo.get("dirty") else "clean"
            changes_note = (
                f" ({repo['changes']} changes)" if repo.get("changes", 0) > 0 else ""
            )
            parts.append(f"### {repo['name']}")
            parts.append("")
            parts.append(f"- **Path**: `{repo['path']}`")
            parts.append(f"- **Branch**: `{repo['branch']}`")
            parts.append(f"- **Status**: {status}{changes_note}")
            parts.append("")

            if repo.get("recent_commits"):
                parts.append("**Recent commits:**")
                parts.append("")
                for commit in repo["recent_commits"]:
                    parts.append(f"- {commit}")
                parts.append("")

        content = "\n".join(parts)
        orch = orchestrator_dir(ticket_dir)
        atomic_write(orch / "WORKSPACE.md", content)
