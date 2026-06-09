"""Tests for the duct workspace command."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from duct.cli.main import cli
from duct.cli.workspace_cmd import (
    RepoCandidate,
    _local_default_branch,
    _org_cache_dir,
    _org_cache_path,
    clone_repo,
    discover_repos,
    find_repo,
    list_branches,
    list_org_repos,
    list_repo_candidates,
)
from duct.config import WorkspaceConfig


def _init_workspace(runner: CliRunner, root: Path) -> None:
    """Run duct init to set up config.yaml in the workspace root."""
    runner.invoke(cli, ["--workspace-root", str(root), "init"])


def _create_ticket_dir(root: Path, key: str, slug: str = "") -> Path:
    """Manually create a ticket directory with orchestrator subdir."""
    dirname = f"{key}-{slug}" if slug else f"{key}-{key.lower()}"
    ticket_dir = root / dirname
    (ticket_dir / "orchestrator").mkdir(parents=True)
    return ticket_dir


def _create_repo(base: Path, name: str) -> Path:
    """Create a fake git repo directory."""
    repo = base / name
    repo.mkdir(parents=True, exist_ok=True)
    (repo / ".git").mkdir()
    return repo


def _init_git_repo(path: Path, branch: str = "main") -> None:
    """Initialize a real git repo at ``path`` with a single commit on ``branch``."""
    git_env = {
        "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    subprocess.run(["git", "init"], cwd=path, capture_output=True)
    subprocess.run(["git", "checkout", "-b", branch], cwd=path, capture_output=True)
    (path / "README.md").write_text("hello")
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=path, capture_output=True, env=git_env,
    )


def _write_repo_paths_config(tmp_path: Path, repos_dir: Path) -> None:
    """Point the workspace config's repoPaths at ``repos_dir``."""
    import yaml
    config_path = tmp_path / "config.yaml"
    cfg_data = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
    cfg_data["repoPaths"] = [str(repos_dir)]
    config_path.write_text(yaml.dump(cfg_data))


# ---------------------------------------------------------------------------
# Repo discovery tests
# ---------------------------------------------------------------------------


def test_discover_repos_flat(tmp_path: Path) -> None:
    """discover_repos finds repos at the top level of repoPaths."""
    _create_repo(tmp_path, "ice-claims")
    _create_repo(tmp_path, "ice-gateway")

    cfg = WorkspaceConfig(repo_paths=[tmp_path])
    repos = discover_repos(cfg)

    names = [name for name, _ in repos]
    assert "ice-claims" in names
    assert "ice-gateway" in names


def test_discover_repos_nested(tmp_path: Path) -> None:
    """discover_repos finds repos nested under organizing directories."""
    _create_repo(tmp_path / "claims", "ice-claims")
    _create_repo(tmp_path / "esb", "ers-claims-feature")
    _create_repo(tmp_path / "contract", "ice-claims-model")

    cfg = WorkspaceConfig(repo_paths=[tmp_path])
    repos = discover_repos(cfg)

    names = [name for name, _ in repos]
    assert "ice-claims" in names
    assert "ers-claims-feature" in names
    assert "ice-claims-model" in names


def test_discover_repos_skips_dotdirs(tmp_path: Path) -> None:
    """discover_repos ignores directories starting with a dot."""
    _create_repo(tmp_path / ".hidden", "secret-repo")
    _create_repo(tmp_path, "visible-repo")

    cfg = WorkspaceConfig(repo_paths=[tmp_path])
    repos = discover_repos(cfg)

    names = [name for name, _ in repos]
    assert "visible-repo" in names
    assert "secret-repo" not in names


def test_discover_repos_skips_worktrees(tmp_path: Path) -> None:
    """discover_repos ignores worktrees (where .git is a file, not a directory)."""
    _create_repo(tmp_path, "real-repo")
    # Simulate a worktree: .git is a file pointing to the main repo
    worktree = tmp_path / "worktree-repo"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: /some/path/to/.git/worktrees/wt")

    cfg = WorkspaceConfig(repo_paths=[tmp_path])
    repos = discover_repos(cfg)

    names = [name for name, _ in repos]
    assert "real-repo" in names
    assert "worktree-repo" not in names


def test_discover_repos_does_not_descend_into_repos(tmp_path: Path) -> None:
    """discover_repos stops descending once it finds a .git directory."""
    parent = _create_repo(tmp_path, "parent-repo")
    # Create a nested dir inside the repo — should not be found as a separate repo
    nested = parent / "packages" / "sub-repo"
    nested.mkdir(parents=True)
    (nested / ".git").mkdir()

    cfg = WorkspaceConfig(repo_paths=[tmp_path])
    repos = discover_repos(cfg)

    names = [name for name, _ in repos]
    assert "parent-repo" in names
    assert "sub-repo" not in names


def test_discover_repos_respects_max_depth(tmp_path: Path) -> None:
    """discover_repos does not scan past max_depth."""
    _create_repo(tmp_path / "a" / "b" / "c" / "d", "deep-repo")

    cfg = WorkspaceConfig(repo_paths=[tmp_path])

    shallow = discover_repos(cfg, max_depth=2)
    assert not any(name == "deep-repo" for name, _ in shallow)

    deep = discover_repos(cfg, max_depth=5)
    assert any(name == "deep-repo" for name, _ in deep)


def test_find_repo_found(tmp_path: Path) -> None:
    repo = _create_repo(tmp_path / "claims", "ice-claims")
    cfg = WorkspaceConfig(repo_paths=[tmp_path])

    result = find_repo(cfg, "ice-claims")
    assert result == repo


def test_find_repo_not_found(tmp_path: Path) -> None:
    cfg = WorkspaceConfig(repo_paths=[tmp_path])
    assert find_repo(cfg, "nonexistent") is None


# ---------------------------------------------------------------------------
# list_repo_candidates tests
# ---------------------------------------------------------------------------


def test_list_repo_candidates_local_only(tmp_path: Path) -> None:
    """With no githubOrgs configured, returns only local repos."""
    _create_repo(tmp_path, "ice-claims")
    cfg = WorkspaceConfig(repo_paths=[tmp_path])

    with patch(
        "duct.cli.workspace_cmd.list_org_repos", return_value=[],
    ):
        candidates = list_repo_candidates(cfg)

    names = [c.name for c in candidates]
    assert names == ["ice-claims"]
    local = candidates[0]
    assert local.is_remote_only is False
    assert local.slug is None


def test_list_repo_candidates_populates_local_default_branch(tmp_path: Path) -> None:
    """Local candidates expose the repo's origin/HEAD as default_branch."""
    repo = tmp_path / "ice-claims"
    repo.mkdir()
    _init_git_repo(repo, branch="main")
    # Simulate ``git clone`` having set the remote HEAD symref.
    subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main"],
        cwd=repo, capture_output=True,
    )
    cfg = WorkspaceConfig(repo_paths=[tmp_path])

    with patch("duct.cli.workspace_cmd.list_org_repos", return_value=[]):
        candidates = list_repo_candidates(cfg)

    assert candidates[0].default_branch == "main"


def test_local_default_branch_missing_symref_returns_none(tmp_path: Path) -> None:
    """Repos without an origin HEAD symref report no default branch."""
    repo = tmp_path / "loose"
    repo.mkdir()
    _init_git_repo(repo, branch="main")

    assert _local_default_branch(repo) is None


def test_list_repo_candidates_merges_remote(tmp_path: Path) -> None:
    """Remote-only repos appear as remote candidates with their slug."""
    _create_repo(tmp_path, "ice-claims")
    cfg = WorkspaceConfig(repo_paths=[tmp_path], github_orgs=("acme",))

    from duct.cli.workspace_cmd import RepoCandidate
    remote = [
        RepoCandidate(name="aa-mocks", slug="acme/aa-mocks", default_branch="main"),
    ]
    with patch(
        "duct.cli.workspace_cmd.list_org_repos", return_value=remote,
    ):
        candidates = list_repo_candidates(cfg)

    by_name = {c.name: c for c in candidates}
    assert "ice-claims" in by_name
    assert "aa-mocks" in by_name
    assert by_name["aa-mocks"].is_remote_only
    assert by_name["aa-mocks"].slug == "acme/aa-mocks"
    assert by_name["ice-claims"].is_remote_only is False


def test_list_repo_candidates_local_wins_on_collision(tmp_path: Path) -> None:
    """When local and remote share a name, local path is preserved."""
    repo = _create_repo(tmp_path, "ice-claims")
    cfg = WorkspaceConfig(repo_paths=[tmp_path], github_orgs=("acme",))

    from duct.cli.workspace_cmd import RepoCandidate
    remote = [
        RepoCandidate(name="ice-claims", slug="acme/ice-claims", default_branch="main"),
    ]
    with patch(
        "duct.cli.workspace_cmd.list_org_repos", return_value=remote,
    ):
        candidates = list_repo_candidates(cfg)

    by_name = {c.name: c for c in candidates}
    merged = by_name["ice-claims"]
    assert merged.local_path == repo
    assert merged.slug == "acme/ice-claims"  # remote slug attached for metadata
    assert merged.is_remote_only is False


# ---------------------------------------------------------------------------
# list_org_repos cache tests
# ---------------------------------------------------------------------------


def test_list_org_repos_writes_cache_on_miss(tmp_path: Path) -> None:
    """First call fetches live, writes a cache file, returns candidates."""
    fetched = [
        RepoCandidate(name="alpha", slug="acme/alpha", default_branch="main"),
    ]
    with patch(
        "duct.cli.workspace_cmd._fetch_org_repos_live", return_value=fetched,
    ) as mock_fetch:
        result = list_org_repos(["acme"], cache_dir=tmp_path)

    assert [c.name for c in result] == ["alpha"]
    mock_fetch.assert_called_once_with("acme")
    cache_file = tmp_path / "acme.json"
    assert cache_file.exists()
    payload = json.loads(cache_file.read_text())
    assert payload["candidates"][0]["slug"] == "acme/alpha"
    assert "fetched_at" in payload


def test_list_org_repos_reads_fresh_cache(tmp_path: Path) -> None:
    """A fresh cache file is reused -- no live fetch happens."""
    cache_file = tmp_path / "acme.json"
    cache_file.write_text(json.dumps({
        "fetched_at": __import__("time").time(),
        "candidates": [
            {"name": "cached", "slug": "acme/cached", "default_branch": "main"},
        ],
    }))

    with patch("duct.cli.workspace_cmd._fetch_org_repos_live") as mock_fetch:
        result = list_org_repos(["acme"], cache_dir=tmp_path)

    mock_fetch.assert_not_called()
    assert [c.name for c in result] == ["cached"]


def test_list_org_repos_refresh_bypasses_cache(tmp_path: Path) -> None:
    """refresh=True forces a live fetch even with a fresh cache."""
    cache_file = tmp_path / "acme.json"
    cache_file.write_text(json.dumps({
        "fetched_at": __import__("time").time(),
        "candidates": [{"name": "old", "slug": "acme/old", "default_branch": None}],
    }))
    refreshed = [
        RepoCandidate(name="new", slug="acme/new", default_branch="main"),
    ]
    with patch(
        "duct.cli.workspace_cmd._fetch_org_repos_live", return_value=refreshed,
    ) as mock_fetch:
        result = list_org_repos(["acme"], cache_dir=tmp_path, refresh=True)

    mock_fetch.assert_called_once_with("acme")
    assert [c.name for c in result] == ["new"]
    # Cache was overwritten with the fresh result.
    payload = json.loads(cache_file.read_text())
    assert [c["name"] for c in payload["candidates"]] == ["new"]


def test_list_org_repos_falls_back_to_stale_cache(tmp_path: Path) -> None:
    """When the live fetch fails, stale cache contents are preferred over nothing."""
    cache_file = tmp_path / "acme.json"
    cache_file.write_text(json.dumps({
        "fetched_at": 0,  # very stale
        "candidates": [
            {"name": "stale", "slug": "acme/stale", "default_branch": "main"},
        ],
    }))

    with patch(
        "duct.cli.workspace_cmd._fetch_org_repos_live", return_value=None,
    ):
        result = list_org_repos(["acme"], cache_dir=tmp_path)

    assert [c.name for c in result] == ["stale"]


def test_list_org_repos_no_cache_dir_calls_live_every_time(tmp_path: Path) -> None:
    """With no cache_dir, the call always fetches live (backwards compat)."""
    fetched = [
        RepoCandidate(name="alpha", slug="acme/alpha", default_branch="main"),
    ]
    with patch(
        "duct.cli.workspace_cmd._fetch_org_repos_live", return_value=fetched,
    ) as mock_fetch:
        list_org_repos(["acme"])
        list_org_repos(["acme"])

    assert mock_fetch.call_count == 2


def test_org_cache_path_namespaces_per_workspace(tmp_path: Path) -> None:
    """Cache files land under <workspace>/.cache/gh-org-repos/<org>.json."""
    p = _org_cache_path(tmp_path, "ice-tech-group")
    assert p == tmp_path / ".cache" / "gh-org-repos" / "ice-tech-group.json"
    # _org_cache_dir is the parent directory.
    assert _org_cache_dir(tmp_path) == p.parent


# ---------------------------------------------------------------------------
# list-repos / list-branches CLI tests
# ---------------------------------------------------------------------------


def test_list_repos_cli_includes_local_and_remote(tmp_path: Path) -> None:
    """`duct list-repos` prints local repos and merged remote repos."""
    runner = CliRunner()
    _init_workspace(runner, tmp_path)

    repos_dir = tmp_path / "repos"
    _create_repo(repos_dir, "ice-claims")
    _write_repo_paths_config(tmp_path, repos_dir)

    from duct.cli.workspace_cmd import RepoCandidate
    remote = [
        RepoCandidate(name="aa-mocks", slug="acme/aa-mocks", default_branch="main"),
    ]
    with patch("duct.cli.workspace_cmd.list_org_repos", return_value=remote):
        result = runner.invoke(
            cli, ["--json", "--workspace-root", str(tmp_path), "workspace", "list-repos"],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    by_name = {row["name"]: row for row in payload}
    assert by_name["ice-claims"]["kind"] == "local"
    assert by_name["aa-mocks"]["kind"] == "remote"
    assert by_name["aa-mocks"]["slug"] == "acme/aa-mocks"
    assert by_name["aa-mocks"]["default_branch"] == "main"


def test_list_repos_cli_text_output_columns(tmp_path: Path) -> None:
    """Human-readable output includes kind, name, default branch, and location."""
    runner = CliRunner()
    _init_workspace(runner, tmp_path)

    repos_dir = tmp_path / "repos"
    repo = repos_dir / "ice-claims"
    repo.mkdir(parents=True)
    _init_git_repo(repo, branch="main")
    _write_repo_paths_config(tmp_path, repos_dir)

    remote = [RepoCandidate(name="mocks", slug="acme/mocks", default_branch="develop")]
    with patch("duct.cli.workspace_cmd.list_org_repos", return_value=remote):
        result = runner.invoke(
            cli, ["--workspace-root", str(tmp_path), "workspace", "list-repos"],
        )

    assert result.exit_code == 0, result.output
    assert "local " in result.output
    assert "ice-claims" in result.output
    assert "remote" in result.output
    assert "mocks" in result.output
    assert "develop" in result.output
    assert "acme/mocks" in result.output


def test_list_repos_refresh_flag_bypasses_cache(tmp_path: Path) -> None:
    """`duct list-repos --refresh` forces a fresh fetch even with a fresh cache."""
    runner = CliRunner()
    _init_workspace(runner, tmp_path)

    # Point cfg.root at tmp_path so cache files land here.
    import yaml
    config_path = tmp_path / "config.yaml"
    cfg_data = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
    cfg_data["workspace"] = {"root": str(tmp_path)}
    cfg_data["githubOrgs"] = ["acme"]
    cfg_data["repoPaths"] = [str(tmp_path / "empty")]
    (tmp_path / "empty").mkdir()
    config_path.write_text(yaml.dump(cfg_data))

    # Seed a fresh cache that should be used when --refresh is omitted.
    cache_path = _org_cache_path(tmp_path, "acme")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({
        "fetched_at": __import__("time").time(),
        "candidates": [
            {"name": "cached", "slug": "acme/cached", "default_branch": "main"},
        ],
    }))

    fresh = [RepoCandidate(name="fresh", slug="acme/fresh", default_branch="main")]
    with patch(
        "duct.cli.workspace_cmd._fetch_org_repos_live", return_value=fresh,
    ) as mock_fetch:
        # Without --refresh, the cache is used and no fetch happens.
        runner.invoke(cli, ["--workspace-root", str(tmp_path), "workspace", "list-repos"])
        mock_fetch.assert_not_called()

        # With --refresh, a live fetch is forced.
        result = runner.invoke(
            cli, ["--workspace-root", str(tmp_path), "workspace", "list-repos", "--refresh"],
        )

    assert result.exit_code == 0, result.output
    assert "fresh" in result.output
    mock_fetch.assert_called_once_with("acme")


def test_list_branches_local_repo(tmp_path: Path) -> None:
    """`duct list-branches <local-repo>` enumerates branches on disk."""
    runner = CliRunner()
    _init_workspace(runner, tmp_path)

    repos_dir = tmp_path / "repos"
    repo = repos_dir / "ice-claims"
    repo.mkdir(parents=True)
    _init_git_repo(repo, branch="main")
    subprocess.run(
        ["git", "branch", "release/2024"], cwd=repo, capture_output=True,
    )
    _write_repo_paths_config(tmp_path, repos_dir)

    result = runner.invoke(
        cli, ["--workspace-root", str(tmp_path), "workspace", "list-branches", "ice-claims"],
    )

    assert result.exit_code == 0, result.output
    branches = {line.strip() for line in result.output.splitlines() if line.strip()}
    assert {"main", "release/2024"}.issubset(branches)


def test_list_branches_remote_only_repo(tmp_path: Path) -> None:
    """`duct list-branches` falls back to remote enumeration via gh api."""
    runner = CliRunner()
    _init_workspace(runner, tmp_path)

    # Configure a GitHub org so the remote lookup runs.
    import yaml
    config_path = tmp_path / "config.yaml"
    cfg_data = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
    cfg_data["githubOrgs"] = ["acme"]
    cfg_data["repoPaths"] = [str(tmp_path / "empty")]
    (tmp_path / "empty").mkdir()
    config_path.write_text(yaml.dump(cfg_data))

    from duct.cli.workspace_cmd import RepoCandidate
    with patch(
        "duct.cli.workspace_cmd.list_org_repos",
        return_value=[RepoCandidate(name="aa-mocks", slug="acme/aa-mocks", default_branch="main")],
    ), patch(
        "duct.cli.workspace_cmd.list_remote_branches",
        return_value=["main", "release/2024"],
    ):
        result = runner.invoke(
            cli,
            ["--json", "--workspace-root", str(tmp_path), "workspace", "list-branches", "aa-mocks"],
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert payload == {"repo": "aa-mocks", "branches": ["main", "release/2024"]}


def test_list_branches_unknown_repo_errors(tmp_path: Path) -> None:
    """Unknown repo names surface a helpful error referencing repoPaths."""
    runner = CliRunner()
    _init_workspace(runner, tmp_path)

    _write_repo_paths_config(tmp_path, tmp_path / "repos")
    (tmp_path / "repos").mkdir()

    with patch("duct.cli.workspace_cmd.list_org_repos", return_value=[]):
        result = runner.invoke(
            cli, ["--workspace-root", str(tmp_path), "workspace", "list-branches", "ghost"],
        )

    assert result.exit_code != 0
    assert "ghost" in result.output
    assert "repoPaths" in result.output


def _clone_repo(remote: Path, dest: Path) -> None:
    """Clone ``remote`` into ``dest`` so ``dest`` has an ``origin`` remote."""
    subprocess.run(
        ["git", "clone", str(remote), str(dest)], capture_output=True,
    )


def test_list_branches_fetches_new_remote_branches(tmp_path: Path) -> None:
    """A branch pushed to origin after cloning shows up in the listing."""
    remote = tmp_path / "remote"
    remote.mkdir()
    _init_git_repo(remote, branch="main")

    clone = tmp_path / "clone"
    _clone_repo(remote, clone)

    # New branch appears on the remote only after the clone was taken.
    subprocess.run(
        ["git", "branch", "release/2099"], cwd=remote, capture_output=True,
    )

    assert "release/2099" in list_branches(clone)


def test_list_branches_prunes_deleted_remote_branches(tmp_path: Path) -> None:
    """A branch deleted on origin drops out of the listing via --prune."""
    remote = tmp_path / "remote"
    remote.mkdir()
    _init_git_repo(remote, branch="main")
    subprocess.run(
        ["git", "branch", "stale/feature"], cwd=remote, capture_output=True,
    )

    clone = tmp_path / "clone"
    _clone_repo(remote, clone)
    assert "stale/feature" in list_branches(clone)

    subprocess.run(
        ["git", "branch", "-D", "stale/feature"], cwd=remote, capture_output=True,
    )

    assert "stale/feature" not in list_branches(clone)


def test_list_branches_no_origin_is_quiet(tmp_path: Path, capsys) -> None:
    """A repo with no origin lists local branches without any fetch warning."""
    repo = tmp_path / "local-only"
    repo.mkdir()
    _init_git_repo(repo, branch="main")
    capsys.readouterr()  # drop git's own output from setup

    branches = list_branches(repo)

    assert "main" in branches
    assert capsys.readouterr().err == ""


def test_list_branches_fetch_failure_warns_and_lists(tmp_path: Path, capsys) -> None:
    """A broken origin yields a stale-list warning but still returns branches."""
    repo = tmp_path / "broken-origin"
    repo.mkdir()
    _init_git_repo(repo, branch="main")
    subprocess.run(
        ["git", "remote", "add", "origin", str(tmp_path / "does-not-exist")],
        cwd=repo, capture_output=True,
    )
    capsys.readouterr()

    branches = list_branches(repo)

    assert "main" in branches
    assert "branch list may be stale" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# workspace status tests
# ---------------------------------------------------------------------------


def test_workspace_status_empty(tmp_path: Path) -> None:
    """workspace status with no tickets should report nothing found."""
    runner = CliRunner()
    _init_workspace(runner, tmp_path)

    result = runner.invoke(cli, ["--workspace-root", str(tmp_path), "workspace", "status"])

    assert result.exit_code == 0, result.output
    assert "No tickets found" in result.output


def test_workspace_status_shows_tickets(tmp_path: Path) -> None:
    """workspace status should list tickets found in the workspace."""
    runner = CliRunner()
    _init_workspace(runner, tmp_path)

    _create_ticket_dir(tmp_path, "PROJ-10")
    _create_ticket_dir(tmp_path, "PROJ-20")

    result = runner.invoke(cli, ["--workspace-root", str(tmp_path), "workspace", "status"])

    assert result.exit_code == 0, result.output
    assert "PROJ-10" in result.output
    assert "PROJ-20" in result.output


def test_workspace_status_json(tmp_path: Path) -> None:
    """workspace status --json should produce parseable JSON output."""
    runner = CliRunner()
    _init_workspace(runner, tmp_path)

    _create_ticket_dir(tmp_path, "PROJ-50")

    result = runner.invoke(
        cli, ["--json", "--workspace-root", str(tmp_path), "workspace", "status"]
    )

    assert result.exit_code == 0, result.output
    data = json.loads(result.output.strip())
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["key"] == "PROJ-50"


def test_workspace_path_found(tmp_path: Path) -> None:
    """workspace path KEY should print the ticket directory path."""
    runner = CliRunner()
    _init_workspace(runner, tmp_path)

    ticket_dir = _create_ticket_dir(tmp_path, "PROJ-99", "some-feature")

    result = runner.invoke(
        cli, ["--workspace-root", str(tmp_path), "workspace", "path", "PROJ-99"]
    )

    assert result.exit_code == 0, result.output
    assert str(ticket_dir) in result.output.strip()


def test_workspace_path_not_found(tmp_path: Path) -> None:
    """workspace path KEY should fail when no matching directory exists."""
    runner = CliRunner()
    _init_workspace(runner, tmp_path)

    result = runner.invoke(
        cli, ["--workspace-root", str(tmp_path), "workspace", "path", "NOPE-1"]
    )

    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# add-repo tests (non-interactive, all args provided)
# ---------------------------------------------------------------------------


def test_add_repo_top_level_all_args(tmp_path: Path) -> None:
    """duct add-repo KEY REPO BASEBRANCH should create a worktree (top-level command)."""
    runner = CliRunner()
    _init_workspace(runner, tmp_path)

    ticket_dir = _create_ticket_dir(tmp_path, "ERSC-100", "some-ticket")
    repo_dir = _create_repo(tmp_path / "repos", "my-repo")

    # Initialize a real git repo so worktree add works
    subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "main"], cwd=repo_dir, capture_output=True)
    (repo_dir / "README.md").write_text("hello")
    subprocess.run(["git", "add", "."], cwd=repo_dir, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo_dir, capture_output=True,
        env={"GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t",
             "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )

    # Write config with repo_paths pointing to our repos dir
    import yaml
    config_path = tmp_path / "config.yaml"
    cfg_data = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
    cfg_data["repoPaths"] = [str(tmp_path / "repos")]
    config_path.write_text(yaml.dump(cfg_data))

    result = runner.invoke(
        cli,
        ["--workspace-root", str(tmp_path), "workspace", "add-repo", "ERSC-100", "my-repo", "main"],
    )

    assert result.exit_code == 0, result.output
    assert "Added worktree" in result.output
    assert (ticket_dir / "my-repo").exists()


def test_add_repo_no_track(tmp_path: Path) -> None:
    """Created worktree branch should have no upstream tracking."""
    runner = CliRunner()
    _init_workspace(runner, tmp_path)

    ticket_dir = _create_ticket_dir(tmp_path, "ERSC-300", "no-track")
    repo_dir = _create_repo(tmp_path / "repos", "track-repo")

    subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "main"], cwd=repo_dir, capture_output=True)
    (repo_dir / "README.md").write_text("hello")
    subprocess.run(["git", "add", "."], cwd=repo_dir, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo_dir, capture_output=True,
        env={"GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t",
             "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )

    import yaml
    config_path = tmp_path / "config.yaml"
    cfg_data = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
    cfg_data["repoPaths"] = [str(tmp_path / "repos")]
    config_path.write_text(yaml.dump(cfg_data))

    runner.invoke(
        cli,
        ["--workspace-root", str(tmp_path),
         "workspace", "add-repo", "ERSC-300", "track-repo", "main"],
    )

    worktree_path = ticket_dir / "track-repo"
    # Check that the branch has no upstream
    check = subprocess.run(
        ["git", "config", "--get", "branch.ersc-300.remote"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    assert check.returncode != 0, "Branch should have no upstream tracking"


def test_add_repo_repo_not_found(tmp_path: Path) -> None:
    """add-repo with a bad repo name should give a helpful error."""
    runner = CliRunner()
    _init_workspace(runner, tmp_path)

    _create_ticket_dir(tmp_path, "ERSC-400", "bad-repo")
    _create_repo(tmp_path / "repos", "real-repo")

    import yaml
    config_path = tmp_path / "config.yaml"
    cfg_data = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
    cfg_data["repoPaths"] = [str(tmp_path / "repos")]
    config_path.write_text(yaml.dump(cfg_data))

    result = runner.invoke(
        cli,
        ["--workspace-root", str(tmp_path),
         "workspace", "add-repo", "ERSC-400", "typo-repo", "main"],
    )

    assert result.exit_code != 0
    assert "typo-repo" in result.output
    assert "real-repo" in result.output


def test_add_repo_writes_sandbox_settings(tmp_path: Path) -> None:
    """add-repo should write .claude/settings.json in the new worktree."""
    runner = CliRunner()
    _init_workspace(runner, tmp_path)

    _create_ticket_dir(tmp_path, "ERSC-600", "sandbox-test")
    repo_dir = _create_repo(tmp_path / "repos", "sandbox-repo")

    subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "main"], cwd=repo_dir, capture_output=True)
    (repo_dir / "README.md").write_text("hello")
    subprocess.run(["git", "add", "."], cwd=repo_dir, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo_dir, capture_output=True,
        env={"GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t",
             "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )

    import yaml
    config_path = tmp_path / "config.yaml"
    cfg_data = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
    cfg_data["repoPaths"] = [str(tmp_path / "repos")]
    config_path.write_text(yaml.dump(cfg_data))

    result = runner.invoke(
        cli,
        ["--workspace-root", str(tmp_path),
         "workspace", "add-repo", "ERSC-600", "sandbox-repo", "main"],
    )

    assert result.exit_code == 0, result.output
    worktree_path = tmp_path / "ERSC-600-sandbox-test" / "sandbox-repo"
    settings = worktree_path / ".claude" / "settings.json"
    assert settings.exists()
    data = json.loads(settings.read_text())
    assert data["sandbox"]["enabled"] is True


def test_add_repo_branch_override(tmp_path: Path) -> None:
    """--branch should override the auto-generated feature branch name."""
    runner = CliRunner()
    _init_workspace(runner, tmp_path)

    _create_ticket_dir(tmp_path, "ERSC-500", "branch-override")
    repo_dir = _create_repo(tmp_path / "repos", "override-repo")

    subprocess.run(["git", "init"], cwd=repo_dir, capture_output=True)
    subprocess.run(["git", "checkout", "-b", "main"], cwd=repo_dir, capture_output=True)
    (repo_dir / "README.md").write_text("hello")
    subprocess.run(["git", "add", "."], cwd=repo_dir, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo_dir, capture_output=True,
        env={"GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t",
             "PATH": "/usr/bin:/bin:/usr/local/bin"},
    )

    import yaml
    config_path = tmp_path / "config.yaml"
    cfg_data = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
    cfg_data["repoPaths"] = [str(tmp_path / "repos")]
    config_path.write_text(yaml.dump(cfg_data))

    result = runner.invoke(
        cli,
        [
            "--workspace-root", str(tmp_path),
            "workspace", "add-repo", "ERSC-500", "override-repo", "main",
            "--branch", "custom-branch-name",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "custom-branch-name" in result.output


# ---------------------------------------------------------------------------
# clone_repo unit tests
# ---------------------------------------------------------------------------


def _fake_clone_success(cmd, **kwargs):
    """Simulate a successful clone — create the dest and drop a .git marker."""
    dest = Path(cmd[-1])
    dest.mkdir(parents=True, exist_ok=False)
    (dest / ".git").mkdir()

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    return _Result()


def test_clone_repo_uses_gh_for_slug(tmp_path: Path) -> None:
    """Slug inputs route through `gh repo clone`."""
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_clone_success(cmd, **kwargs)

    with patch("duct.cli.workspace_cmd.subprocess.run", side_effect=fake_run):
        result = clone_repo("my-org/my-repo", tmp_path)

    assert captured["cmd"][:3] == ["gh", "repo", "clone"]
    assert captured["cmd"][3] == "my-org/my-repo"
    assert result == tmp_path / "my-repo"
    assert (tmp_path / "my-repo" / ".git").exists()


def test_clone_repo_uses_git_for_url(tmp_path: Path) -> None:
    """URL inputs route through `git clone` and strip a trailing .git."""
    captured: dict = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _fake_clone_success(cmd, **kwargs)

    with patch("duct.cli.workspace_cmd.subprocess.run", side_effect=fake_run):
        result = clone_repo("git@github.com:my-org/my-repo.git", tmp_path)

    assert captured["cmd"][:2] == ["git", "clone"]
    assert result == tmp_path / "my-repo"


def test_clone_repo_rejects_existing_dest(tmp_path: Path) -> None:
    """Refuses to clone over a directory that already exists."""
    (tmp_path / "my-repo").mkdir()

    import pytest
    with pytest.raises(RuntimeError, match="already exists"):
        clone_repo("my-org/my-repo", tmp_path)


def test_clone_repo_raises_on_failure(tmp_path: Path) -> None:
    """Non-zero return code is surfaced as a RuntimeError."""
    def fake_run(cmd, **kwargs):
        class _Result:
            returncode = 1
            stdout = ""
            stderr = "repository not found"
        return _Result()

    import pytest
    with patch("duct.cli.workspace_cmd.subprocess.run", side_effect=fake_run):
        with pytest.raises(RuntimeError, match="repository not found"):
            clone_repo("my-org/missing", tmp_path)


# ---------------------------------------------------------------------------
# add-repo --clone-from integration tests
# ---------------------------------------------------------------------------


def test_add_repo_clones_missing_repo(tmp_path: Path) -> None:
    """--clone-from populates the first repoPath when the repo isn't present."""
    runner = CliRunner()
    _init_workspace(runner, tmp_path)
    _create_ticket_dir(tmp_path, "ERSC-700", "clone-missing")

    repos_dir = tmp_path / "repos"
    repos_dir.mkdir()
    _write_repo_paths_config(tmp_path, repos_dir)

    def fake_clone(clone_from: str, dest_parent: Path) -> Path:
        dest = dest_parent / "new-repo"
        dest.mkdir()
        _init_git_repo(dest)
        return dest

    with patch("duct.cli.workspace_cmd.clone_repo", side_effect=fake_clone):
        result = runner.invoke(
            cli,
            [
                "--workspace-root", str(tmp_path),
                "workspace", "add-repo", "ERSC-700", "new-repo", "main",
                "--clone-from", "my-org/new-repo",
            ],
        )

    assert result.exit_code == 0, result.output
    assert "cloning from my-org/new-repo" in result.output
    assert (tmp_path / "ERSC-700-clone-missing" / "new-repo").exists()


def test_add_repo_clone_from_is_noop_when_repo_present(tmp_path: Path) -> None:
    """--clone-from is ignored (no clone attempt) when the repo exists locally."""
    runner = CliRunner()
    _init_workspace(runner, tmp_path)
    _create_ticket_dir(tmp_path, "ERSC-710", "clone-noop")

    repos_dir = tmp_path / "repos"
    repo_dir = _create_repo(repos_dir, "present-repo")
    _init_git_repo(repo_dir)
    _write_repo_paths_config(tmp_path, repos_dir)

    with patch("duct.cli.workspace_cmd.clone_repo") as mock_clone:
        result = runner.invoke(
            cli,
            [
                "--workspace-root", str(tmp_path),
                "workspace", "add-repo", "ERSC-710", "present-repo", "main",
                "--clone-from", "my-org/present-repo",
            ],
        )

    assert result.exit_code == 0, result.output
    assert "--clone-from ignored" in result.output
    mock_clone.assert_not_called()


def test_add_repo_clone_from_errors_without_repopaths(tmp_path: Path) -> None:
    """--clone-from errors clearly when no repoPaths are configured."""
    runner = CliRunner()
    _init_workspace(runner, tmp_path)
    _create_ticket_dir(tmp_path, "ERSC-720", "clone-no-paths")

    # Overwrite config with an empty repoPaths list.
    import yaml
    config_path = tmp_path / "config.yaml"
    cfg_data = yaml.safe_load(config_path.read_text()) if config_path.exists() else {}
    cfg_data["repoPaths"] = []
    config_path.write_text(yaml.dump(cfg_data))

    with patch("duct.cli.workspace_cmd.clone_repo") as mock_clone:
        result = runner.invoke(
            cli,
            [
                "--workspace-root", str(tmp_path),
                "workspace", "add-repo", "ERSC-720", "missing-repo", "main",
                "--clone-from", "my-org/missing-repo",
            ],
        )

    assert result.exit_code != 0
    assert "no repoPaths configured" in result.output
    mock_clone.assert_not_called()


def test_api_add_repo_clone_from_clones_missing(tmp_path: Path) -> None:
    """api.add_repo(..., clone_from=...) clones a missing repo before worktree creation."""
    from duct import api

    runner = CliRunner()
    _init_workspace(runner, tmp_path)
    _create_ticket_dir(tmp_path, "ERSC-740", "api-clone")

    repos_dir = tmp_path / "repos"
    repos_dir.mkdir()
    _write_repo_paths_config(tmp_path, repos_dir)

    called_with: dict = {}

    def fake_clone(clone_from: str, dest_parent: Path) -> Path:
        called_with["clone_from"] = clone_from
        called_with["dest_parent"] = dest_parent
        dest = dest_parent / "api-repo"
        dest.mkdir()
        _init_git_repo(dest)
        return dest

    with patch("duct.cli.workspace_cmd.clone_repo", side_effect=fake_clone):
        worktree = api.add_repo(
            tmp_path, "ERSC-740", "api-repo", "main", "feature-x",
            clone_from="acme/api-repo",
        )

    assert called_with["clone_from"] == "acme/api-repo"
    assert called_with["dest_parent"] == repos_dir
    assert worktree.exists()


def test_api_add_repo_skips_clone_when_local(tmp_path: Path) -> None:
    """api.add_repo does not attempt clone when the repo already exists locally."""
    from duct import api

    runner = CliRunner()
    _init_workspace(runner, tmp_path)
    _create_ticket_dir(tmp_path, "ERSC-741", "api-clone-noop")

    repos_dir = tmp_path / "repos"
    repo_dir = _create_repo(repos_dir, "api-present")
    _init_git_repo(repo_dir)
    _write_repo_paths_config(tmp_path, repos_dir)

    with patch("duct.cli.workspace_cmd.clone_repo") as mock_clone:
        api.add_repo(
            tmp_path, "ERSC-741", "api-present", "main", "feature-y",
            clone_from="acme/api-present",
        )

    mock_clone.assert_not_called()


def test_add_repo_clone_name_mismatch_errors(tmp_path: Path) -> None:
    """If the cloned directory name doesn't match REPO_NAME, surface a clear error."""
    runner = CliRunner()
    _init_workspace(runner, tmp_path)
    _create_ticket_dir(tmp_path, "ERSC-730", "clone-mismatch")

    repos_dir = tmp_path / "repos"
    repos_dir.mkdir()
    _write_repo_paths_config(tmp_path, repos_dir)

    def fake_clone(clone_from: str, dest_parent: Path) -> Path:
        # Produce a dir that doesn't match repo_name ('expected-name').
        dest = dest_parent / "actual-name"
        dest.mkdir()
        _init_git_repo(dest)
        return dest

    with patch("duct.cli.workspace_cmd.clone_repo", side_effect=fake_clone):
        result = runner.invoke(
            cli,
            [
                "--workspace-root", str(tmp_path),
                "workspace", "add-repo", "ERSC-730", "expected-name", "main",
                "--clone-from", "my-org/actual-name",
            ],
        )

    assert result.exit_code != 0
    assert "actual-name" in result.output
    assert "expected-name" in result.output
