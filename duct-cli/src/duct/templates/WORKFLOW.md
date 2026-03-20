# Workflow

This document is guidance for the orchestrator, not a rigid pipeline. The orchestrator reads it on each run and applies judgement about what to do next. There are no mandatory stages or fixed sequences — just concerns to address, heuristics to guide decisions, and standards to measure against.

## Development Concerns

A ticket is done when all relevant concerns are addressed. Not every concern applies to every ticket — the orchestrator evaluates which are unmet and decides what to tackle next.

- **Context** — Does BACKGROUND.md capture the business context, domain knowledge, and "why" behind this ticket?
- **Acceptance criteria** — Does AC.md define what "done" looks like with enough specificity to implement and verify against?
- **Specification** — For non-trivial changes, does SPEC.md describe the technical approach? Simple tickets may skip this.
- **Workspace** — Are the necessary repo worktrees set up with appropriately named branches?
- **Implementation** — Is the work progressing? See "Assessing Implementation Progress" below.
- **Verification** — Does VERIFICATION.md show evidence the work meets the acceptance criteria?
- **Code review** — Are PRs created, reviewed, and feedback addressed?
- **Deployment** — For changes with deployment concerns (migrations, config changes), does DEPLOYMENT.md capture what's needed?
- **QA** — If the ticket warrants it, does QA.md describe how to test?
- **Completion** — Are PRs merged, CI passing, and the ticket ready for transition?

## Decision Heuristics

- If a ticket has no BACKGROUND.md, start there — context informs everything else.
- If BACKGROUND.md exists but AC.md doesn't, draft acceptance criteria next.
- If AC.md exists but the workspace isn't set up, propose workspace creation.
- If the workspace exists but no implementation has started, consider whether SPEC.md is needed first.
- If implementation hasn't started and the ticket is straightforward, propose launching a Claude session with a specific prompt referencing AC.md.
- If a session is active and progressing, leave it alone — don't propose competing actions.
- If a session terminated but the work looks incomplete (no PR, partial commits), note what was accomplished and consider whether to propose a follow-up session.
- If a session appears stuck (idle for a long time, last messages suggest confusion or a blocker), flag it and describe the apparent blocker.
- If commits exist but no PR, check whether the work looks complete relative to AC.md before proposing PR creation.
- If PRs exist with review feedback, addressing reviews takes priority over new work.
- If CI is failing, that's urgent — investigate before moving other work forward.
- If a ticket has been idle for a long time, flag it in ORCHESTRATOR.md.
- If multiple concerns are unmet, prefer addressing them roughly in the order listed above — but use judgement. A developer who's already implementing doesn't need you to go back and write BACKGROUND.md.

## Attention and Priority

- Read PRIORITY.md first. It tells you where to focus.
- Each entry must be a markdown list item (`- `) containing a ticket key. You may add sections, notes, and formatting around entries.
- Urgent signals override stated priorities: failing CI, unaddressed review feedback, sessions waiting for input.
- You can update PRIORITY.md if signals warrant it (e.g., a CI failure bumps a ticket up).
- Spread attention across the portfolio — don't tunnel-vision on one ticket while others stall.

## Quality Standards per Artifact

What "good" looks like for each authored artifact:

- **BACKGROUND.md** — Captures the problem being solved, the business motivation, relevant domain context, related tickets/history. A developer reading this should understand *why* this work matters without needing to read the Jira ticket.
- **AC.md** — Specific, testable criteria. Goes beyond Jira AC to include technical requirements, edge cases, and definition of done. Each criterion should be verifiable.
- **SPEC.md** — Design decisions, approach, affected components, data flow. Explains *how* the work will be done. Should be proportional to complexity — a one-line config change doesn't need a spec.
- **IMPLEMENTATION.md** — Written after implementation. Explains what changed and why. The narrative companion to the diff.
- **VERIFICATION.md** — Evidence that AC is met. Test results, manual testing notes, coverage data. Not just "tests pass" — shows which criteria were verified and how.
- **DEPLOYMENT.md** — Deployment prerequisites, manual steps, environment considerations, rollback plan. Only needed when deployment isn't trivial.
- **QA.md** — What to test, how, expected results. Written for the QA team or developer's own testing.
- **ORCHESTRATOR.md** — Your working notes. Observations, decisions, blockers, reasoning. Read your own previous notes to maintain continuity across runs.

## Assessing Implementation Progress

Implementation isn't binary — it's a spectrum from "not started" to "done and verified." The orchestrator has several signals available, each telling a different part of the story.

### Signals from the orchestrator/ directory

- **WORKSPACE.md** (sync snapshot) — Shows git branch state, uncommitted changes, and worktree health. Key questions: Does the branch have commits beyond the base? Are there uncommitted changes that suggest active work? Is the worktree clean or dirty?
- **CLAUDE_SESSIONS.md** (sync snapshot) — Shows active, idle, and recently terminated Claude Code sessions working on this ticket. Key questions: Is there an active session right now? Did a recent session complete its work or get stuck? What files did sessions touch?
- **PULL_REQUESTS.md** (sync snapshot) — Shows whether PRs exist and their state. A PR's existence means implementation reached a point the developer considered ready for review.
- **CI.md** (sync snapshot) — Build results. Passing CI on a PR branch is a strong signal that implementation is functionally complete. Failing CI tells you what's broken.

### Signals from the worktree itself

The orchestrator can also Read, Glob, and Grep files in the repo worktrees directly. This is useful when sync snapshots don't tell the full story:
- Check `git log` output in WORKSPACE.md for commit messages that indicate progress
- Look at recently modified files to understand the scope of changes so far
- Compare what's been changed against what AC.md requires to estimate completeness

### Interpreting signals together

- No branch commits, no sessions, no PRs → implementation hasn't started. Consider whether SPEC.md is needed or if a session should be launched.
- Active session, dirty worktree, no PR → implementation is in progress. Check CLAUDE_SESSIONS.md for blockers or stalled state.
- Terminated session, commits on branch, no PR → session finished some work but didn't create a PR. Check whether the work is complete or if more sessions are needed.
- PR exists, CI failing → implementation submitted but has issues. Check CI.md for failure details.
- PR exists, CI passing, review requested → implementation is complete and awaiting review. The concern shifts to "Code review."
- PR exists, changes requested → review feedback needs addressing. This takes priority.

### What to record in ORCHESTRATOR.md

When assessing implementation, note the concrete state you observed — not just "in progress" but "3 commits on branch, last session terminated 2 hours ago after editing ClaimService.java, no PR yet." This specificity helps you (on future runs) and the developer understand exactly where things stand.

## Action Types

This section defines what actions the orchestrator can take, when each is appropriate, and what context to provide.

### Artifact actions

- **Write artifact** — Create or update an authored file (BACKGROUND.md, AC.md, SPEC.md, etc.) in the ticket's orchestrator/ directory. When to use: whenever a concern is unmet and you can address it by writing. This is your primary output.

### Git actions

- **Git commit** — Commit staged changes in a ticket's worktree. When to use: after writing implementation code, fixing a bug, or addressing review feedback. Include: the commit message you'd use and which files are being committed.
- **Git push** — Push a branch to the remote. When to use: after committing, when the branch is ready for others to see (typically before PR creation or after addressing review feedback). Include: the branch name and remote.

### GitHub actions

- **PR creation** — Create a pull request from the ticket's branch. When to use: implementation is complete, tests pass, the work is ready for review. Include: target branch, PR title, description draft, and which reviewers to request.
- **PR merge** — Merge an approved pull request. When to use: PR is approved, CI is passing, no outstanding feedback. Include: the PR URL and merge method.

### Jira actions

- **Jira comment** — Post a comment on the Jira ticket. When to use: to communicate progress, flag blockers, or summarise completed work. Include: the comment text. Keep it concise and useful to humans reading the ticket.
- **Jira transition** — Move the ticket to a different status. When to use: when the ticket's actual state clearly matches a different Jira status (e.g., work is complete, PR is merged → transition to Done). Include: the target status and evidence supporting the transition.

### Time tracking actions

- **Time log** — Log time spent on the ticket. When to use: when observable activity (session durations, commit timestamps) provides a reasonable basis for a time entry. Include: duration, description of work, and the evidence you used to estimate.

### Launching sessions

- **Launch Claude session** — Start a new Claude Code session to work on a specific task for a ticket. When to use: when implementation needs to start or continue, review feedback needs addressing, or a specific technical task needs doing. Include: the working directory, a specific prompt that references the relevant artifacts (AC.md, review comments, etc.), and what the session should accomplish.

## Working Notes

Always update ORCHESTRATOR.md after evaluating a ticket. Record:
- What you observed (artifact state, sync snapshot signals, blockers)
- What action you took or proposed
- What you chose not to do and why

This gives you continuity across runs and gives the developer transparency into your reasoning.

## Agent Prompts

Specific workflow tasks (writing background documents, drafting acceptance criteria, designing specifications, reviewing code) will have dedicated agent prompt templates that provide detailed, structured guidance. These are being developed separately. In the meantime, the orchestrator should write specific, contextual prompts when launching sessions — referencing the relevant artifacts (AC.md, review comments, SPEC.md) and clearly describing what the session should accomplish.
