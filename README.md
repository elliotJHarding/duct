<p align="center">
  <img src="logo.svg" alt="duct logo" width="120"/>
</p>

<h1 align="center">duct</h1>

Developer workflow orchestration CLI. Syncs data from Jira and GitHub into a ticket-centric folder structure and provides an AI orchestrator (Claude Code session) that reviews state and produces artifacts.

## Install

```bash
brew tap elliotJHarding/duct
brew install duct
```

This installs both the `duct` CLI and the `duct-tui` terminal UI.

## Quick Start

```bash
cd duct-cli
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

duct init --workspace-root ~/workspace/duct
```

## Configuration

Set environment variables for external service auth:

```bash
export JIRA_EMAIL="you@company.com"
export JIRA_TOKEN="your-jira-api-token"
export GH_TOKEN="your-github-pat"
```

Edit `toolkit/config.yaml` in your workspace to configure the Jira domain, JQL query, repo paths, and sync intervals.

## Commands

```
duct init                          Create workspace skeleton
duct config                        View configuration
duct sync                          Run all sync sources
duct sync jira                     Sync Jira tickets
duct sync github                   Sync GitHub pull requests
duct sync ci                       Sync CI status
duct sync sessions                 Sync Claude Code session data
duct sync workspace                Sync local git workspace state
duct ticket list                   List tracked tickets
duct ticket status <KEY>           Show ticket details and artifacts
duct workspace create <KEY>        Create workspace for a ticket
duct workspace add-repo <KEY> <R>  Add a repo worktree
duct workspace status              Show workspace health
duct archive list                  List archived tickets
duct archive restore <KEY>         Restore an archived ticket
duct orchestrate                   Launch orchestrator session
duct orchestrate --ticket <KEY>    Focus on a specific ticket
```

All commands support `--json` for structured output and `--workspace-root` to override the workspace location.

## Workspace Structure

```
{workspace_root}/
    toolkit/                       # tracked config + knowledge (its own git repo)
        config.yaml
        WORKFLOW.md
        CLAUDE.md
        agents/                    # reusable session prompts
        wiki/                      # curated knowledge base (INDEX.md + entries)
        subagents/                 # wiki-reader / -contributor / -maintainer
        settings.template.json
    .claude/                       # generated from toolkit/ (CLAUDE.md shim + agent copies)
    .duct/                         # runtime state (daemon, runs/, activity/, cache/,
                                   #   sync_state.yaml, review_prs.md, actions.yaml)
    epics/                         # epic metadata
    {TICKET_KEY}-{slug}/           # ticket dirs live flat at the workspace root
        orchestrator/
            TICKET.md              # sync: Jira data
            PULL_REQUESTS.md       # sync: GitHub PRs
            CI.md                  # sync: build status
            CLAUDE_SESSIONS.md     # sync: active sessions
            WORKSPACE.md           # sync: git state
            RESEARCH.md            # authored: context
            AC.md                  # authored: acceptance criteria
            SPEC.md                # authored: technical spec
            ORCHESTRATOR.md        # authored: working notes
        repo-worktree/
    .archive/
```

Files with `source: sync` frontmatter are overwritten each sync cycle. Authored files are created by the developer or orchestrator and persist. `toolkit/` is a git repo you can track and carry between machines; everything else is machine-local state.

## Development

```bash
source .venv/bin/activate
pytest                  # run tests
ruff check src/ tests/  # lint
```

## Architecture

duct is a Python package with a library-first design. The CLI (`duct.cli`) is a thin wrapper around the library. A Python TUI can import the library directly; a Rust TUI can use subprocess + `--json` output.

See [SPEC.md](SPEC.md) for the full specification.
