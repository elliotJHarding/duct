# duct CLI — command tree

Global options: `--json`, `--debug`. Commands listed as shown by `duct --help` (alphabetical); `init` is hidden.

```
duct
├── activity                 Aggregate an activity log across Jira, GitHub, git, Claude, Outlook
│   ├── gather               Fetch events from each source and append to the JSONL store
│   ├── log                  Render the stored activity log for a window
│   └── providers            Show providers, per-provider last-run, and event counts
├── agent                    Manage and launch workflow agents
│   ├── list                 List available agents
│   └── run                  Launch a Claude Code session using the named agent as its prompt
├── archive                  List and manage archived tickets
│   ├── add                  Archive a ticket (move it to .archive/)
│   ├── list                 List archived tickets
│   └── restore              Restore an archived ticket to the workspace
├── config                   View or edit workspace configuration
│   ├── set                  Set a configuration value using dotted key paths
│   ├── add-repo-path        Add a directory to the repoPaths list
│   └── remove-repo-path     Remove a directory from the repoPaths list
├── daemon                   Manage the duct background daemon (notifications, sync, scheduling)
│   ├── run                  Run the daemon loop in the foreground (what launchd executes)
│   ├── install              Install + load the launchd agent (runs at every login)
│   ├── uninstall            Stop + remove the launchd agent
│   ├── start                Start (or restart) the installed daemon
│   ├── stop                 Stop the running daemon
│   ├── status               Report installed/running state and heartbeat age
│   └── notify               Fire a notification via the daemon's notification mechanism
├── doctor                   Validate config, credentials, prerequisites (default = health check)
│   ├── perf                 Show timing statistics from ~/.duct/perf.jsonl
│   └── completion           Print shell completion activation script (bash|zsh|fish)
├── orchestrate              Launch an orchestrator Claude Code session
├── pr                       List and inspect pull requests
│   ├── list                 List pull requests for tracked tickets
│   ├── open                 Open a pull request in the browser
│   └── review               List PRs needing review, or deep-review one by number
│       ├── list             List PRs that need your review
│       └── <#>              Check out PR #<#> locally and open it in IntelliJ
├── session                  View and manage Claude Code sessions
│   ├── list                 List Claude Code sessions with ticket mapping
│   ├── show                 Show details for a specific session
│   ├── start                Launch a Claude Code session focused on a specific ticket
│   └── jump                 Jump to the terminal tab running a session
├── setup                    Walk through every prerequisite duct needs to run
├── status                   Show a unified dashboard of all tracked work
├── sync                     Run sync sources (no subcommand = run all)
│   ├── jira                 Sync Jira tickets
│   ├── github               Sync GitHub pull requests
│   ├── ci                   Sync CI/build status
│   ├── sessions             Sync Claude session data
│   ├── workspace            Sync local workspace state
│   ├── claude-md            Refresh per-ticket CLAUDE.md files
│   └── status               Show last sync time and staleness per source
├── ticket                   List and inspect tracked tickets
│   ├── list                 List all tracked tickets
│   ├── open                 Open a ticket's Jira page in the browser
│   └── status               Show ticket details and artifact inventory
├── wiki                     Inspect and maintain the workspace wiki
│   ├── list                 List wiki entries
│   ├── show                 Show a wiki entry by name
│   └── review               Run the wiki-maintainer subagent to dedupe and prune the wiki
├── workspace                Manage ticket workspaces
│   ├── add-repo             Add a repo worktree to a ticket workspace
│   ├── list-branches        List branches for a repo (local or configured GitHub org)
│   ├── list-repos           List every repo available to this workspace
│   ├── path                 Print the workspace path for a ticket
│   └── status               Show workspace health across all tickets
└── init                     Create workspace skeleton (hidden; scriptable)
```

Recent reorg: top-level `add-repo` / `list-repos` / `list-branches` aliases removed (use `workspace …`);
`perf` and `completion` moved under `doctor`; `notify` moved under `daemon`; `ticket show` → `ticket status`;
`pr deep-review` → `pr review <#>` and the old `pr review` listing → `pr review list`.
