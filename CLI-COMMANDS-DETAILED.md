# duct CLI — detailed reference (current)

Examples run against `~/workspace/duct/`. Output is trimmed; wide tables abbreviated. Commands with side effects (sync, orchestrate, archive add/restore, add-repo, agent run, session start, daemon run/install, config set, notify, activity gather, wiki review) show syntax only.

---

## activity — Jira/GitHub/git/Claude/Outlook activity log

### activity providers
```
$ duct activity providers
        Activity Providers
 Provider     Enabled  Last Run               Events
 jira         yes      2026-06-08T08:30:31Z      752
 github       yes      2026-06-08T08:30:31Z      199
 git          yes      2026-06-08T08:30:31Z      347
 claude       yes      2026-06-08T08:30:31Z      543
 outlook      yes      2026-06-08T08:30:31Z      322
 outlook_pdf  yes      2026-06-08T08:30:31Z       57
```

### activity log
```
$ duct activity log --since 1d
No activity between 2026-06-08 14:16 and 2026-06-09 14:16 UTC.
```
Options: `--since`, `--until`, `--format markdown|jsonl|json`, `--ticket`, `--source`.

### activity gather  (side effects — appends to JSONL store)
```
$ duct activity gather --since 1d [--provider jira]
```

---

## agent — workflow agents

### agent list
```
$ duct agent list
                Agents
 Name                   Description
 acceptance-criteria    Disambiguate client-agreed Jira acceptance criteria…
 draft-standup-update   Draft a team standup update…
 draft-timesheet        Draft a timesheet from .activity/ logs…
 enter-timesheet-avaza  Enter a drafted timesheet into Avaza…
 pr-review              Pick up to 3 open PRs needing review…
 research               Research a ticket's business and technical context…
```

### agent run  (side effects — launches Claude Code)
```
$ duct agent run <name> --ticket <KEY> [--repo <repo>] [--skip-permissions]
```

---

## archive — archived tickets

### archive list
```
$ duct archive list
                Archived Tickets
 Key        Directory
 ACM-646    ACM-646-load-alert-details-double-loading
 ACM-647    ACM-647-aa-policy-fetch-esb-caching
 ERSC-1276  ERSC-1276-verisk2-pre-populate-total-loss-screen
 …
```

### archive add / restore  (side effects — moves ticket dir)
```
$ duct archive add <KEY>        # move KEY into .archive/
$ duct archive restore <KEY>    # move KEY back into the workspace
```

---

## config — workspace configuration

### config  (view)
```
$ duct config
root: /Users/hardinge/workspace/duct
jira_domain: iceinsuretech.atlassian.net
jira_jql: assignee = currentUser() AND status != Done ORDER BY updated DESC
repo_paths:
- /Users/hardinge/workspace
- /Users/hardinge/projects
sync_intervals:
  jira: 600
  github: 600
  sessions: 900
  workspace: 600
  ci: 600
  activity: 900
  claude_md: 0
```

### config set / add-repo-path / remove-repo-path  (side effects — writes config.yaml)
```
$ duct config set jira.jql "assignee = currentUser() ORDER BY updated DESC"
$ duct config add-repo-path /Users/hardinge/projects
$ duct config remove-repo-path /Users/hardinge/projects
```
Settable keys: `jira.domain`, `jira.jql`, `syncIntervals.*`, `display.nerdFont`, `activity.outlookPdfPath`.

---

## daemon — background service

### daemon status
```
$ duct daemon status
Installed: yes
Loaded/running: yes
Last heartbeat: 2s ago
```

### daemon run / install / uninstall / start / stop  (side effects — launchd)
```
$ duct daemon install      # install + load launchd agent
$ duct daemon start        # start (or restart)
$ duct daemon stop         # stop
$ duct daemon uninstall    # stop + remove
$ duct daemon run          # foreground loop (what launchd executes)
```

---

## doctor — health check
```
$ duct doctor
Workspace ───────────────────────────────────────────
  OK  config.yaml found (/Users/hardinge/workspace/duct/config.yaml)
  OK  config.yaml parses
  OK  jira.domain set (iceinsuretech.atlassian.net)
  OK  jira.jql set
  OK  WORKFLOW.md exists
Authentication ──────────────────────────────────────
  OK  Jira email set    OK  Jira token set    OK  GitHub token
API Reachability ────────────────────────────────────
  OK  Jira API reachable (Elliot Harding)
  OK  GitHub API reachable (elliot-harding-ice)
Tools ───────────────────────────────────────────────
  OK  claude / git / gh CLI on PATH    OK  mmdc on PATH (optional)
Daemon ──────────────────────────────────────────────
  OK  daemon installed
```

---

## daemon notify  (side effects — fires a notification)
```
$ duct daemon notify --title "Build done" --body "ICEC-1559 green" [--ticket ICEC-1559] [--url <url>]
```

---

## orchestrate  (side effects — launches an orchestrator Claude Code session)
```
$ duct orchestrate [--ticket <KEY>] [--sync] [--dry-run] [--skip-permissions] [-v]
```

---

## doctor perf — timing statistics
```
$ duct doctor perf
Span timings (over last 2000 entries, ms)
  span                       count    p50     p95     max    total
  git.status_branch            155  812.1  3375.7  3700.9  187127.9
  tui.refresh_sessions         123  319.1   841.4  1324.0   49529.1
  wezterm.get-text            1026   23.9   100.4   359.5   40938.4
  ps                           481   59.8   134.6   266.5   33682.5
```
Options: `--limit`, `--name`, `--tail`.

---

## pr — pull requests

### pr list
```
$ duct pr list
                       Pull Requests
 Ticket     #     Repo
 AZIE-1593  1826  ice-tech-group/ice-claims
 ICEC-1735  5     ice-tech-group/ice-private-invoice-api
 KAM-1856   3     ice-tech-group/aa-to-kam-partner-api-credit-service-jobs
 PS-6271    16    ice-tech-group/allianz-gsss-financial-sanctions
 …
```
Options: `[KEY]`, `--all`, `--closed`, `--state open|merged|closed`.

### pr review list
```
$ duct pr review list
              Awaiting Your Review
 Repo                        Author   Why
 ice-tech-group/ice-claims   @har…    @ice-tech-group/claims-tech-leads…
 ice-tech-group/claims-mocks @har…    @ice-tech-group/claims-dev…
 …
```
Bare `duct pr review` defaults to `review list`.

### pr review <#> / pr open  (side effects — browser / IntelliJ checkout)
```
$ duct pr open <number>          # open the PR in the browser
$ duct pr review <number>        # check out the PR locally and open it in IntelliJ
```

---

## session — Claude Code sessions

### session list
```
$ duct session list
                 Claude Sessions
 Status   PID    Ticket     Topic                              Session ID
 done     19388  AZIE-1593  Review and locally test workflow…  64991f39-613
 ready    8254   KAM-1856   Review Elko's PR comment           7e221128-e2c
 ready    27494  PS-6270    Add specification for fix to Jira  c06be2d9-09c
 working  79963  -          Review and improve CLI command…    1914ad73-d05
```
Option: `--all`.

### session show
```
$ duct session show <session_id>
```

### session start / jump  (side effects — launches/focuses a terminal session)
```
$ duct session start <KEY>
$ duct session jump <session_id>
```

---

## setup  (interactive — guided onboarding)
```
$ duct setup
```

---

## status — unified dashboard
```
$ duct status
                              duct Status
 Key        Status        Category            PRs  CI     Sessions  Dirty  Sync
 KAM-1856   Deployed      Awaiting Action       9  mixed         1      6    4m
 AZIE-1593  In Progress   Active Development    1  mixed         1      4    4m
 ICEC-1638  In Progress   Active Development    -  -             -      4    4m
 ICEC-1735  Deployed      Awaiting Action       2  mixed         -      2    4m
 …
```
Options: `--all` (everything except terminal statuses), `--closed` (include Closed/Done).

---

## sync — refresh state  (side effects — writes synced data to disk)

### sync status  (read-only)
```
$ duct sync status
                Sync Status
 Source      Last Sync   Interval  Stale
 ci          4m ago         10m    no
 claude_md   28s ago         0s    yes
 github      10m ago        10m    yes
 jira        4m ago         10m    no
 sessions    7m ago         15m    no
 workspace   4m ago         10m    no
```

### sync (all) / per-source
```
$ duct sync [--force]              # run all sources
$ duct sync jira | github | ci | sessions | workspace | claude-md
```

---

## ticket — tracked tickets

### ticket list
```
$ duct ticket list
                       Tracked Tickets
 Key        Summary                            Status            Category
 ACM-624    Copart Phase 1 - Send Instruction  To Do             Pre-Development
 AZIE-1593  Trigger - Claim Experience Feed…   In Progress       Active Development
 DEP-2053   ERS - CLAIMS - PRODUCTION…         Closed            Done
 ICEC-1559  Claims API improvements            To Do             Pre-Development
 …
```
Options: `--category`, `--status`, `--sort key|status|category`.

### ticket status
```
$ duct ticket status ICEC-1559
ICEC-1559: Claims API improvements

Status:    To Do        Category:  Pre-Development
Priority:  Medium       Type:      Epic        Assignee:  Elliot Harding
[View in Jira](https://iceinsuretech.atlassian.net/browse/ICEC-1559)

## Description
Claims API improvements.

## Available Transitions
- Start Progress
- No Fix Required

Artifacts ───────────────  TICKET.md
Repo worktrees ──────────  .claude
```

### ticket open  (side effects — browser)
```
$ duct ticket open <KEY>
```

---

## wiki — workspace wiki

### wiki list
```
$ duct wiki list
                   Wiki entries
 Name                                    Type        Description
 assert-json-payload-from-test-resources convention  Stores expected JSON payloads…
 …
```

### wiki show
```
$ duct wiki show assert-json-payload-from-test-resources
name: assert-json-payload-from-test-resources
type: convention
description: Stores expected JSON payloads under src/test/resources/payload/<area>…

# Assert JSON payloads from test resources, not inline literals
## Rule
For integration / Camel-route tests asserting a complete JSON body, store the
expected payload as a file under src/test/resources/payload/<area>/<scenario>.json…
## Why  …
## How to apply  …
```

### wiki review  (side effects — launches wiki-maintainer subagent)
```
$ duct wiki review
```

---

## workspace — ticket worktrees & repo discovery

### workspace status
```
$ duct workspace status
                       Workspace Status
 Key        Artifacts  Repos  Path
 AZIE-1593     14       5     /Users/hardinge/workspace/duct/AZIE-1593-tr…
 ICEC-1638      8       4     /Users/hardinge/workspace/duct/ICEC-1638-cl…
 KAM-1856      23       8     /Users/hardinge/workspace/duct/KAM-1856-aa-…
 ICEC-1559      1       0     /Users/hardinge/workspace/duct/ICEC-1559-cl…
 …
```

### workspace path
```
$ duct workspace path ICEC-1559
/Users/hardinge/workspace/duct/ICEC-1559-claims-api-improvements
```

### workspace list-repos
```
$ duct workspace list-repos
remote  aa-aas-claims                          master           ice-tech-group/aa-aas-claims
local   aa-accman-claims-super-feature         release/23.3.1   /Users/hardinge/workspace/esb/aa-accman-claims-super-feature
local   aa-to-kam-partner-api-credit-service…  release/1.0      /Users/hardinge/workspace/duct/KAM-1856-…/aa-to-kam-…
…
```
Option: `--refresh` (bypass 6h GitHub-org cache).

### workspace list-branches
```
$ duct workspace list-branches ice-claims
25.2.0.AZIE.1---Pre-test-failures
AZIE-1582-UI-Linking-of-a-Task-to-a-Casefile-Item-directly
ICEC-1638-claims-apis-poc
…
```

### workspace add-repo  (side effects — creates a worktree)
```
$ duct workspace add-repo <KEY> <repo> [base-branch] [--branch <name>] [--clone-from <url|org/repo>]
```

---

## doctor completion — shell completion script
```
$ duct doctor completion zsh        # then: eval "$(duct doctor completion zsh)"
$ duct doctor completion bash | fish
```
