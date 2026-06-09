"""Tests for the hidden `duct migrate-layout` one-time migration."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from duct import paths, run_lock
from duct.cli.main import cli
from duct.cli.migrate_cmd import migrate_workspace_layout, planned_workspace_moves


def _old_layout_workspace(root: Path) -> None:
    """Scaffold a pre-restructure workspace (config + state loose at root)."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "config.yaml").write_text("workspace:\n  root: .\n")
    (root / "WORKFLOW.md").write_text("# workflow\n")
    (root / "wiki").mkdir()
    (root / "wiki" / "INDEX.md").write_text("# Wiki Index\n")
    (root / "agents").mkdir()
    (root / "agents" / "spec.md").write_text("---\nname: spec\n---\nbody\n")
    claude_agents = root / ".claude" / "agents"
    claude_agents.mkdir(parents=True)
    (root / ".claude" / "CLAUDE.md").write_text("# orientation\n")
    for name in ("wiki-reader", "wiki-contributor", "wiki-maintainer"):
        (claude_agents / f"{name}.md").write_text(f"# {name}\n")
    (root / ".runs").mkdir()
    (root / ".runs" / "2026-01-01T00-00-00.md").write_text("---\nexit_code: 0\n---\n")
    (root / ".actions.yaml").write_text("actions: []\n")
    (root / ".sync_state.yaml").write_text("{}\n")
    # A ticket dir whose CLAUDE.md carries the OLD relative imports.
    ticket = root / "PROJ-1-feature"
    (ticket / "orchestrator").mkdir(parents=True)
    (ticket / "orchestrator" / "TICKET.md").write_text("---\nsource: sync\n---\n")
    (ticket / "CLAUDE.md").write_text(
        "<!-- BEGIN DUCT MANAGED -->\n@../wiki/INDEX.md\n@../WORKFLOW.md\n"
        "<!-- END DUCT MANAGED -->\n"
    )


class TestApply:
    def test_relocates_files_and_rebuilds(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        _old_layout_workspace(root)

        migrate_workspace_layout(root, apply=True)

        # A — config + knowledge under toolkit/
        assert paths.config_file(root).exists()
        assert paths.workflow_md(root).exists()
        assert paths.wiki_index(root).exists()
        assert (paths.agents_dir(root) / "spec.md").exists()
        assert paths.toolkit_claude_md(root).read_text() == "# orientation\n"
        assert {p.name for p in paths.subagents_dir(root).glob("*.md")} == {
            "wiki-reader.md", "wiki-contributor.md", "wiki-maintainer.md",
        }
        # D — runtime state under .duct/
        assert (paths.runs_dir(root) / "2026-01-01T00-00-00.md").exists()
        assert paths.workspace_actions_file(root).exists()
        assert paths.sync_state_file(root).exists()
        # toolkit is now a git repo
        assert (paths.toolkit_dir(root) / ".git").exists()
        # generated root .claude/ shim + subagent copies
        assert "@../toolkit/wiki/INDEX.md" in paths.root_claude_md(root).read_text()
        assert (paths.root_claude_agents_dir(root) / "wiki-reader.md").exists()
        # old loose files are gone
        assert not (root / "config.yaml").exists()
        assert not (root / ".runs").exists()

    def test_ticket_claude_md_imports_rewritten(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        _old_layout_workspace(root)

        migrate_workspace_layout(root, apply=True)

        body = (root / "PROJ-1-feature" / "CLAUDE.md").read_text()
        assert "@../toolkit/wiki/INDEX.md" in body
        assert "@../toolkit/WORKFLOW.md" in body
        assert "@../wiki/INDEX.md" not in body

    def test_idempotent(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        _old_layout_workspace(root)
        migrate_workspace_layout(root, apply=True)
        # Second run finds nothing left to move.
        assert planned_workspace_moves(root) == []


class TestDryRun:
    def test_lists_moves_without_touching_disk(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        _old_layout_workspace(root)

        result = CliRunner().invoke(cli, ["--workspace-root", str(root), "migrate-layout"])

        assert result.exit_code == 0
        assert "Dry run" in result.output
        assert "config.yaml" in result.output
        # Nothing moved.
        assert (root / "config.yaml").exists()
        assert not paths.config_file(root).exists()


class TestDaemonGuard:
    def test_refuses_when_run_lock_held(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        _old_layout_workspace(root)
        assert run_lock.acquire(root)  # simulate a live orchestrator run

        result = CliRunner().invoke(
            cli, ["--workspace-root", str(root), "migrate-layout", "--apply"]
        )

        assert result.exit_code == 1
        assert "daemon" in result.output.lower()
        assert (root / "config.yaml").exists()  # untouched
