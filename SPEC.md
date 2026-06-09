# duct — Specification

This is a living document. It captures the philosophy, data model, and intended behaviour of duct. It should be refined collaboratively before and during implementation. Where details are unresolved, they are marked as open questions.

duct is a developer workflow tool that keeps agentic software development moving. It syncs data from external systems (Jira, GitHub) into a ticket-centric folder structure, and provides an AI orchestrator that reviews the state of active work, proposes next steps, and produces artifacts. The developer and orchestrator share a filesystem — either can do work, and the system derives state from what actually exists.


## Core Principles

These are the foundational commitments that shape every design decision. If a proposed feature or implementation contradicts these principles, the proposal is wrong.

### Filesystem as database

All state lives as markdown and YAML files in a hierarchical folder structure. There is no SQLite database, no proprietary format, no binary blobs. The folder structure is the data model.

This means a developer can inspect, edit, and diff everything duct knows using standard tools — a text editor, `grep`, `git diff`. The tool enhances the data but never owns it exclusively. If duct stops running, the data remains useful and navigable.

This is a deliberate departure from the previous Houston implementation, which used SQLite and required the application to access its own state. That made the data opaque and tightly coupled to the UI.

### Derived state, not managed state

The system never "owns" state. It reads what actually exists — files on disk, git status, Jira API responses — and derives the current picture from that reality. It does not matter whether the developer or the orchestrator created a file, made a commit, or moved a ticket. The filesystem is the source of truth.

This means there is no state to get out of sync. If the developer manually writes a BACKGROUND.md, the orchestrator sees it exists and moves on. If the orchestrator creates an AC.md and the developer edits it, the edited version is what matters. There is no "expected state" vs "actual state" — there is only actual state.

This principle extends to the sync layer. Sync reads external systems and writes snapshots to disk. The orchestrator reads those snapshots alongside everything else. The orchestrator has no special knowledge about whether data came from Jira or was written by hand — it reads the same files either way.

### The orchestrator is a Claude Code agent

The orchestrator is not a script that assembles prompts and pipes them to an API. It is a Claude Code session, launched with `--add-dir` pointing at the duct workspace. It has access to Read, Glob, Grep, Write, and Edit — the same tools any Claude Code session uses to navigate and modify a codebase.

The orchestrator is given its goals and workflow guidance in its system prompt (via WORKFLOW.md and its launch configuration). It is not given ticket data in the prompt. It reads ticket data by exploring the filesystem, the same way a developer would browse the folder structure. This means the orchestrator scales naturally — it reads what it needs, when it needs it, rather than being front-loaded with everything.

This is a fundamental shift from the Houston orchestrator, which assembled a large prompt containing a ticket index, signals, pending actions, and rejected actions. That approach hit context limits and required careful prompt engineering. The duct orchestrator sidesteps this by treating the filesystem as its context.

### No rigid stages

The development lifecycle (Review, AC, Workspace, Spec, Implement, Test, PR, Merge, Deploy, QA) is a reference guide, not a state machine. Artifacts can be created, updated, or revisited at any time. There are no enforced gates, no "you must complete stage N before starting stage N+1."

The orchestrator uses the lifecycle as a checklist of concerns — things that should be true for a ticket to be considered done. It checks which concerns are not yet addressed and uses its judgement about what to do next, informed by the reference but not constrained by it.

Real work is non-linear. A developer might start implementing and realise the acceptance criteria are wrong. QA might fail and send things back to implementation. The orchestrator must be pragmatic enough to handle this. The goal is to deliver work efficiently to a high standard, not to march through a pipeline.

### Sync produces read-only snapshots

Files written by the sync process — TICKET.md, PULL_REQUESTS.md, CI.md, CLAUDE_SESSIONS.md — are overwritten completely each sync cycle. They are snapshots of external system state at a point in time. Neither the developer nor the orchestrator should edit them, because edits will be lost on next sync.

Content authored by the developer or orchestrator goes in separate files: BACKGROUND.md, AC.md, SPEC.md, ORCHESTRATOR.md, and so on. This separation is the ownership boundary. Sync-generated files have `source: sync` in their YAML frontmatter. Authored files do not.

### Portfolio view

The orchestrator sees all active tickets holistically. It is not scoped to a single ticket — it has access to the entire workspace and can reason about priorities, dependencies, and competing demands across the full portfolio.

PRIORITY.md at the workspace root is a shared document. Both the developer and orchestrator can edit it to signal what should be focused on. The orchestrator uses it to decide where to spend its attention, but can also update it based on what it observes (e.g. a CI failure might warrant bumping a ticket's priority).

### Tiered trust, configurable

Not all actions carry the same risk. Reading files and writing markdown artifacts is low-risk. Creating a git commit is medium-risk. Posting a comment on a Jira ticket or merging a PR is high-risk.

The trust model is configurable per action type. The developer sets their comfort level, and the system respects it. At one end of the spectrum, the orchestrator auto-executes everything. At the other, it proposes everything and waits for approval. Most setups will be somewhere in between.

Write-back to external systems (Jira status transitions, PR comments, time logging) follows the same tiered model. The default should be conservative — propose and wait — with the developer opting in to automation as trust builds.

### Tool-agnostic data format

Markdown with YAML frontmatter. No binary formats, no proprietary schemas, no database locks. Any tool — the CLI, a TUI, a text editor, a shell script, another AI agent — can read and write the same files. The CLI is the primary interface but never the only one.


## The Workspace

The duct workspace is a configurable root directory that contains all active work. It is not a hidden config directory — it is the developer's actual workspace. Code is written here.

### Folder structure

```
{workspace_root}/
    PRIORITY.md
    WORKFLOW.md
    config.yaml
    .claude/
        CLAUDE.md                    # persistent context for orchestrator sessions

    epics/
        {EPIC_KEY}-{epic-title}.md   # epic metadata (source: sync)

    {TICKET_KEY}-{ticket-title}/     # all tickets live at root level
        orchestrator/
            EPIC.md -> ../../epics/{EPIC_KEY}-{epic-title}.md   # symlink to parent epic
            TICKET.md                # sync snapshot: Jira ticket data
            PULL_REQUESTS.md         # sync snapshot: GitHub PR data
            CI.md                    # sync snapshot: build/deploy status
            CLAUDE_SESSIONS.md       # sync snapshot: active Claude sessions
            WORKSPACE.md             # sync snapshot: local workspace state
            PROPOSED_ACTIONS.md      # orchestrator: actions awaiting approval
            BACKGROUND.md            # authored: business context research
            AC.md                    # authored: detailed acceptance criteria
            SPEC.md                  # authored: technical specification
            IMPLEMENTATION.md        # authored: explanation of changes
            VERIFICATION.md         # authored: test results and evidence
            DEPLOYMENT.md            # authored: deployment concerns
            QA.md                    # authored: QA testing plan
            ORCHESTRATOR.md          # authored: orchestrator's working notes
        repo-1/                      # git worktree
        repo-2/                      # git worktree

    {TICKET_KEY}-{ticket-title}/     # tickets without an epic (no EPIC.md)
        orchestrator/
            ...
        repo-1/

    .archive/                        # archived tickets removed from Jira query
        {TICKET_KEY}-{ticket-title}/
            orchestrator/            # preserved
```

### Workspace root

The workspace root is configurable. It defaults to `~/workspace/duct/` but can be set in config. Everything the system manages lives under this root.

### Ticket directories

Each ticket gets a directory named `{TICKET_KEY}-{ticket-title}` (e.g. `ERSC-1278-fix-auth-middleware`) directly under the workspace root. The title is slugified from the Jira summary for human readability. If the ticket belongs to an epic, its `orchestrator/` directory gets an `EPIC.md` symlink pointing to the epic's metadata file in the `epics/` directory.

### The orchestrator/ subdirectory

Each ticket directory contains an `orchestrator/` subdirectory. This is where all sync snapshots and authored artifacts live. Keeping them in a single subdirectory means the ticket root is clean — it contains only repo worktrees and the orchestrator folder.

### Repo worktrees

Repositories live as git worktrees directly inside the ticket directory. `repo-1/` is a real git worktree where the developer writes code. The workspace IS the developer's workspace — there is no separate "code goes here" location.

Worktrees are created during workspace setup. The orchestrator (or developer) determines which repositories need changes for a given ticket and creates ticket-specific worktrees with appropriately named branches. duct uses the developer's existing clones rather than managing bare repos — config specifies `repoPaths` (e.g. `['~/workspace', '~/projects']`) which duct searches to find local clones. If a repo isn't found locally, it can be cloned on demand.

Workspace creation is collaborative: the orchestrator proposes repos and branches, the developer can granularly accept, modify, or add repos manually.

### Root-level files

- **PRIORITY.md** — Shared priority list. Both the developer and orchestrator can edit this. The orchestrator uses it to decide what to focus on and can update it based on observed signals.
- **WORKFLOW.md** — The orchestrator's reference guide. Describes the development lifecycle, what "good" looks like, and how to reason about next steps. This file is guidance, not enforcement.
- **config.yaml** — Workspace-level configuration (data sources, sync intervals, trust tiers, repo paths).
- **.claude/CLAUDE.md** — Persistent context for orchestrator sessions. Describes the workspace structure, conventions, and references WORKFLOW.md. This file is loaded automatically by Claude Code when sessions launch from the workspace root.


## Data Sources and Sync

Sync is a separate operation from orchestration. It pulls data from external systems and writes read-only snapshot files into each ticket's `orchestrator/` directory. Sync runs on a schedule or on demand. Each data source has its own cadence.

### Ticket discovery

The workspace mirrors Jira assignment. Tickets are discovered via a configurable JQL query. The default is:

```
assignee = currentUser() AND status != Done ORDER BY updated DESC
```

When a ticket drops from the query results (e.g. it moves to Done, or is reassigned), it is archived to `{workspace_root}/.archive/{TICKET_KEY}/`. Worktrees are removed to reclaim disk space, but the `orchestrator/` directory is preserved so that authored artifacts and history remain accessible.

### Auth

Authentication uses environment variables only — no keychain, no credentials file.

- `JIRA_EMAIL` — Jira account email
- `JIRA_TOKEN` — Jira API token
- `GH_TOKEN` — GitHub personal access token (also used by `gh` CLI)

### Sources

- **Jira** — Full ticket detail: summary, description, status, assignee, priority, type, epic linkage, comments, linked issues, sprint, fix version, components, labels, subtasks. Everything available from the API is pulled. Writes TICKET.md. Determines the folder structure (which tickets exist, epic grouping). Epic grouping is derived from the Jira epic link field. Tickets without an epic sit at the workspace root level.
- **GitHub** — Pull requests associated with ticket keys (matched by branch name or PR title), review status, comments, requested reviewers. Writes PULL_REQUESTS.md.
- **CI** — Build and deployment status from GitHub Actions and other CI systems. Writes CI.md as its own sync snapshot, separate from PULL_REQUESTS.md. This separation supports non-PR CI like deploy pipelines that aren't tied to a specific pull request.
- **Claude sessions** — See "Claude session sync" below. Writes CLAUDE_SESSIONS.md.
- **Local workspace** — Git status, branch info, uncommitted changes, repo health. Writes WORKSPACE.md.

### Claude session sync

The source of truth for Claude Code sessions is the transcript data on disk at `~/.claude/`. This is local-only — there is no API call, just filesystem reads.

**Discovery**

Sessions are discovered from two locations:

- `~/.claude/sessions/*.json` — Registry files for currently running sessions. Each file is named by PID and contains the session ID, working directory, and start time. Stale registry files (where the PID is no longer alive) are ignored.
- `~/.claude/projects/{encoded-cwd}/{sessionId}.jsonl` — Full conversation transcripts. The encoded-cwd is derived from the session's working directory (leading `/` stripped, remaining `/` replaced with `-`). These persist after a session ends.

Sessions are matched to tickets by extracting ticket keys from the working directory path (regex: `[A-Z]+-\d+`). A session whose cwd is `/Users/dev/workspace/duct/ERSC-1278-fix-auth/ice-claims` matches ticket ERSC-1278.

**What gets extracted from transcripts**

The JSONL transcript is the richest data source. Each line is a JSON object — either a user message, an assistant message, or a file-history snapshot. The sync reads these to extract:

- **Status** — Active (PID alive, transcript recently modified), idle (PID alive, waiting for user input), or terminated (PID dead). Determined by combining the session registry with transcript mtime and the role of the last message.
- **Session title** — From `~/.claude/history.jsonl`, which maps session IDs to display names (typically the first user prompt, truncated).
- **Conversation summary** — The last few exchanges (user prompts and assistant text responses), giving the orchestrator a quick read on what the session was doing and where it left off.
- **Tool usage** — Which tools the session invoked (Read, Edit, Bash, etc.) and on which files. This tells the orchestrator what files were touched without having to diff the worktree.
- **Token usage** — Input/output/cache token counts from assistant message metadata. Useful for cost tracking and understanding session intensity.
- **Duration and timeline** — Start time from registry or first message timestamp, last activity from transcript mtime, total elapsed time.

**What CLAUDE_SESSIONS.md looks like**

The snapshot should give the orchestrator enough context to understand what Claude sessions have done and are doing for this ticket, without requiring it to parse raw JSONL. A session that completed its work and a session that got stuck mid-task look different — the orchestrator needs to tell them apart.

The exact markdown format will be defined during implementation, but it should include per-session blocks with: session ID, status, title, start/end times, a recent conversation excerpt, and a summary of files modified.

**Scope**

Sync includes both active sessions and recently terminated sessions (configurable lookback window). Historical sessions older than the window are excluded to keep the snapshot focused on current and recent activity.

### Sync model

Each source is synced independently. Staleness is tracked per-source — a source is only re-fetched if enough time has elapsed since the last sync (configurable intervals). Sync is idempotent: running it twice produces the same output.

Snapshot files are overwritten completely on each sync. There is no merging, no diffing against previous content. The file represents "what the external system looks like right now."

### Snapshot file format

Each sync-generated file uses YAML frontmatter:

```yaml
---
source: sync
syncedAt: 2026-03-16T10:30:00Z
---
```

The body is structured markdown — tables, headings, lists — formatted for both human reading and programmatic parsing by the orchestrator.


## The Orchestrator

The orchestrator is a Claude Code session that reviews the state of the duct workspace and takes action to keep work moving.

### How it launches

The orchestrator is invoked as a Claude Code session with:
- `--add-dir {workspace_root}` — giving it access to the entire workspace
- `.claude/CLAUDE.md` at the workspace root provides persistent context (workspace structure, conventions, reference to WORKFLOW.md). This is loaded automatically by Claude Code.
- `-p` flag carries run-specific instructions — e.g. "review all tickets and update priorities" vs "focus on ERSC-1278, the AC needs revision"
- `--allowedTools` dynamically built from the trust tier configuration. At low trust: Read, Glob, Grep, Write, Edit. Higher trust adds Bash (which gives the orchestrator access to `git`, `gh`, and other CLI tools).

It is not given ticket data in its prompt. It discovers ticket data by exploring the filesystem.

### What it sees

On each run, the orchestrator:
- Reads PRIORITY.md to understand current focus
- Scans ticket directories to discover active work
- Reads sync snapshots to understand external state (ticket status, PR reviews, CI results)
- Reads authored artifacts to understand progress (does AC.md exist? is SPEC.md complete?)
- Checks repo worktrees for git state (uncommitted changes, branch status)

From this, it derives a picture of what's happening and what needs attention.

### What it does

The orchestrator's primary output is authored artifacts. It writes BACKGROUND.md after researching a ticket. It drafts AC.md after analyzing requirements. It writes ORCHESTRATOR.md with its observations and reasoning. It updates PRIORITY.md when signals warrant it.

For actions that affect external systems (Jira transitions, PR comments, time logging), behaviour depends on the trust tier configuration. Low-trust: the orchestrator writes proposed actions to `PROPOSED_ACTIONS.md` in the ticket's `orchestrator/` directory and waits for developer approval. High-trust: it executes directly. Proposed actions are per-ticket, not global — the developer reviews them in the context of the ticket they relate to.

### WORKFLOW.md as guidance

WORKFLOW.md describes the development lifecycle as a combination of:
- A checklist of concerns — things that should be true for a ticket to be done
- Decision heuristics — "if X then consider Y" patterns
- Quality standards — what "good" looks like for each artifact

The orchestrator reads this file and uses its judgement. It is not a script to be executed mechanically. The orchestrator should be pragmatic — if a ticket doesn't need a detailed spec (e.g. a simple config change), it should recognise that and skip it.

### PRIORITY.md as focus

PRIORITY.md is a shared artifact. The developer writes it to say "focus on ERSC-1278 today." The orchestrator reads it to prioritise its attention. The orchestrator can also update it — for example, if it detects failing CI on a ticket, it might bump that ticket's priority.

The format is deliberately simple (a markdown list of ticket keys with optional notes) so that both humans and the orchestrator can read and write it without friction.

### Triggering

The orchestrator can be triggered:
- **Manually** — `duct orchestrate` runs a single orchestration cycle
- **Scheduled** — a cron job or daemon runs it on an interval

Manual triggering is the primary mode during early development. Scheduled runs are an opt-in as the system matures and trust builds.


## Workflow Artifacts

These are the files the orchestrator and developer produce during the lifecycle of a ticket. They live in the ticket's `orchestrator/` directory. None are mandatory — which artifacts exist depends on what the ticket needs.

### Reference artifacts (authored)

- **BACKGROUND.md** — Business context, domain research, related tickets, historical context. Produced by reviewing the ticket and its surrounding context. Helps the developer (or another agent) understand the "why" behind the work.

- **AC.md** — Detailed acceptance criteria and verification gates. Goes beyond the Jira ticket's AC to specify technical requirements, test expectations, edge cases, and definition of done. This is the contract the implementation is built against.

- **SPEC.md** — Technical specification. Design decisions, approach, affected components, data flow changes. Informed by BACKGROUND.md and AC.md. Not every ticket needs one — a simple bug fix may go straight from AC to implementation.

- **IMPLEMENTATION.md** — Explanation of what was changed and why. Written after implementation, either by the orchestrator or the developer. Serves as the narrative companion to the diff.

- **VERIFICATION.md** — Evidence that the work meets AC.md. Test results, manual testing notes, screenshots, coverage data. The "proof" artifact.

- **DEPLOYMENT.md** — Deployment concerns, prerequisite manual steps, environment-specific considerations, rollback plans. Especially important for changes with database migrations or config changes.

- **QA.md** — QA testing plan. What to test, how to test it, expected results. Written for the QA team or for the developer's own testing.

- **ORCHESTRATOR.md** — The orchestrator's working notes for this ticket. Observations, decisions, blockers, reasoning. This is the orchestrator's scratchpad — it reads its own previous notes to maintain continuity across runs.

### Sync snapshots (read-only)

- **TICKET.md** — Jira ticket data: summary, status, priority, type, assignee, components, labels, description, comments, linked issues.
- **PULL_REQUESTS.md** — GitHub PR data: title, status, branch, review status, CI status, comments, requested reviewers.
- **CI.md** — Build and deployment status: pipeline results, deployment environments, release versions.
- **CLAUDE_SESSIONS.md** — Active and recent Claude Code sessions working on this ticket. Includes status, conversation excerpts, tool usage, and files modified. Derived from transcript JSONL files on disk.
- **WORKSPACE.md** — Local workspace state: git branches, uncommitted changes, worktree health.


## Cross-cutting Concerns

These concerns apply across the lifecycle and are not tied to a specific artifact or stage.

### Ticket updates

The orchestrator should post progress updates on Jira tickets at appropriate moments — when significant milestones are reached (PR created, tests passing, ready for review). The frequency and content of updates is governed by the trust tier and WORKFLOW.md guidance.

### Time logging

Development time should be tracked and logged. The mechanism (Tempo, Avaza, manual) is configurable. The orchestrator can draft time entries based on observed activity (session durations, commit timestamps) and propose them for approval.

### Escalation and communication

The orchestrator should recognise situations that require human judgement or collaboration — ambiguous requirements, cross-team dependencies, blocked tickets with no clear resolution. Rather than making assumptions, it should flag these clearly in ORCHESTRATOR.md and surface them through PRIORITY.md.


## Configuration

Configuration lives in `config.yaml` at the workspace root. Key fields:

```yaml
workspace:
  root: ~/workspace/duct

jira:
  jql: "assignee = currentUser() AND status != Done ORDER BY updated DESC"

repoPaths:
  - ~/workspace
  - ~/projects

trust:
  writeArtifact: auto
  gitCommit: propose
  gitPush: propose
  jiraComment: propose
  jiraTransition: deny
  prCreate: propose
  prMerge: deny
  timeLog: propose
```

### Trust configuration

Each action type maps to one of three modes:
- **auto** — The orchestrator executes the action without asking.
- **propose** — The orchestrator writes the proposed action to `PROPOSED_ACTIONS.md` in the relevant ticket's orchestrator directory and waits for developer approval.
- **deny** — The orchestrator never attempts this action.

The `--allowedTools` passed to the orchestrator's Claude Code session are derived from this config. If all external-system actions are set to `deny`, the orchestrator only gets Read, Glob, Grep, Write, Edit. If `gitCommit` or `gitPush` are `auto` or `propose`, Bash is added to the allowed tools list.

### Repo paths

`repoPaths` tells duct where to search for existing local clones when setting up worktrees. It walks these directories looking for repos that match what a ticket needs. This avoids duct managing its own clones — it uses whatever the developer already has checked out.

### Auth

Authentication is via environment variables (`JIRA_EMAIL`, `JIRA_TOKEN`, `GH_TOKEN`). No credentials are stored in config.


## CLI

The CLI is the core interface. All functionality is exposed through it. TUIs are separate projects that build on the CLI's capabilities.

### Architecture

duct is a Python package (`duct-cli/src/duct/`) with a library-first design. The library exposes a public API that any consumer can import directly. The CLI is a thin wrapper around the library, not the other way around.

All commands support a `--json` flag for structured output, making the CLI usable as a machine interface. A Python TUI imports the library directly. A Rust TUI (or any other consumer) can use subprocess + JSON output.

### Commands

```
duct
    init                    Create config.yaml, PRIORITY.md, WORKFLOW.md, .claude/
    config                  View/edit workspace configuration

    sync                    Run all sync sources
    sync jira               Sync Jira tickets
    sync github             Sync GitHub PRs
    sync ci                 Sync CI/build status
    sync sessions           Sync Claude session data
    sync workspace          Sync local workspace state

    orchestrate             Run an orchestration cycle
    orchestrate --ticket X  Focus orchestration on a specific ticket

    ticket list             List tracked tickets
    ticket status <KEY>     Show ticket details and artifact status

    workspace create <KEY>  Create workspace for a ticket
    workspace add-repo      Add a repo worktree to a ticket workspace
    workspace status        Show workspace health across all tickets

    archive                 Archive done/unassigned tickets
    archive list            List archived tickets
    archive restore <KEY>   Restore an archived ticket

    priority                Show/edit PRIORITY.md
    priority set            Set the priority list
```

`duct init` creates the workspace skeleton only — config.yaml, PRIORITY.md, WORKFLOW.md, and the `.claude/` directory. It does not sync. The developer runs `duct sync` separately as a distinct step. This keeps init fast and predictable.

Command details, arguments, and flags will be specified during implementation.


## TUI

The TUI is a separate project built on top of the CLI. There may be multiple TUI implementations (Rust, Python) sharing the same underlying CLI and data model. The TUI provides a visual dashboard for browsing tickets, viewing artifacts, monitoring sync status, and interacting with the orchestrator. A Python TUI imports the duct library directly. A Rust TUI uses subprocess calls to the CLI with `--json` output.


## Open Questions

These are unresolved decisions. They should be discussed and resolved before or during implementation.

- **Conflict handling** — What happens if the developer and orchestrator edit the same authored file simultaneously? Current assumption is this is rare enough to handle manually. May need file-level locking or last-write-wins with git history as recovery.

- **Multi-developer** — How does the system behave when two developers work on the same duct workspace? Is the workspace per-developer or shared? PRIORITY.md assumes a single developer's focus.

- **Session resumption** — The orchestrator runs as discrete sessions. How much continuity does it maintain between runs? ORCHESTRATOR.md serves as its memory, but should there be a more structured "last run summary" mechanism?

- **Artifact staleness** — Sync snapshots have timestamps. Authored artifacts don't have a built-in freshness model. If BACKGROUND.md was written a month ago and the ticket has changed significantly, how does the orchestrator know to revisit it?

- **Offline mode** — If external APIs are unreachable, sync fails. Should the system degrade gracefully (use stale snapshots) or refuse to operate? The orchestrator can still reason about local state even without fresh sync data.

- **Markdown format evolution** — Sync snapshot formats start from Houston's proven markdown structures, with improvements where appropriate. The exact format of each snapshot file (TICKET.md, PULL_REQUESTS.md, etc.) will be defined during implementation.

- **Archive retention policy** — Archived tickets preserve orchestrator artifacts but remove worktrees. Should there be a configurable retention period, or do archives persist indefinitely until manually cleaned?
