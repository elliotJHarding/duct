# Workflow

This document is guidance for the orchestrator, not a rigid pipeline. The orchestrator reads it on each run and applies judgement about what to do next. There are no mandatory stages or fixed sequences — just concerns to address, heuristics to guide decisions, and standards to measure against.

## Development Concerns

A ticket is done when all relevant concerns are addressed. Not every concern applies to every ticket — the orchestrator evaluates which are unmet and decides what to tackle next.

- **Context** — Does RESEARCH.md capture the business context, domain knowledge, and "why" behind this ticket?
- **Acceptance criteria** — Does AC.md define what "done" looks like with enough specificity to implement and verify against?
- **Specification** — For non-trivial changes, does SPEC.md describe the technical approach? Simple tickets may skip this.
- **Workspace** — Are the necessary repo worktrees set up with appropriately named branches?
- **Implementation** — Is the work progressing? See "Assessing Implementation Progress" below.
- **Verification** — Does VERIFICATION.md show evidence the work meets the acceptance criteria?
- **Code review** — Are PRs created, reviewed, and feedback addressed?
- **Deployment** — For changes with deployment concerns (migrations, config changes), does DEPLOYMENT.md capture what's needed?
- **QA** — If the ticket warrants it, does QA.md describe how to test?
- **Completion** — Are PRs merged, CI passing, and the ticket ready for transition?

## Types of tickets
- **Feature Work** -- Billable work requested by a client. A technical lead and BA create a user story and agree a set of Acceptance criteria to deliver. Tech lead will normally do a high level specification for work then hand to developer
- **Customer Support** -- Tickets  in the PS (Production support) Linked to zendesk tickets and raised by clients through our support team. Support team triages zendesk and raises Jira if needs developer intervention. Must manage ticket with support team to resolve bug to completion
- **Bugs** -- Usually raised by client under customer jira project during UAT of a project. Or could be an internal bug found during regression testing e.g. CREG (Claims REGression) 
- **Tech Debt / Maintenance** -- ICEC (ICE Claims) Tickets usually are internal tech debt or maintenance tasks owned by the team.

## Decision Heuristics

**Default to action.** When in doubt, move the ticket forward. An active session, a posted comment, or a clearly surfaced blocker is always preferable to a silent observation.

- First step of a ticket is understanding core goals. What needs to be delivered or actioned to complete, note this down to help see what needs to be done.
- If a ticket has no repo worktrees yet, propose `workspace` before anything else — subsequent agents work better against live worktrees.
- If implementation hasn't started and the ticket is straightforward, propose launching a Claude session with a specific prompt referencing AC.md.
- If a session is active and progressing, leave it alone — don't propose competing actions. Progress means recent tool use, recent commits, or recent assistant text; a session open but idle for hours is not progressing.
- If a session terminated but the work looks incomplete (no PR, partial commits), propose a follow-up session with a specific brief on what remains — don't merely note it.
- If a session appears stuck, act: propose a fresh session with a tighter brief, or a `jira_comment` if external input is what's missing. Leaving the blocker in ORCHESTRATOR.md is a record, not a resolution.
- If commits exist but no PR, check whether the work looks complete relative to AC.md before proposing PR creation.
- If PRs exist with review feedback, addressing reviews should come before new work — propose a session to address them.
- If CI is failing, that's urgent — investigate before moving other work forward, and propose a session to fix it.
- An idle ticket is a signal to act, not a status to log. Identify the next unmet concern and address it: propose an action, post a Jira comment, or escalate in the run summary so the developer can unblock it.
- If multiple concerns are unmet, prefer addressing them roughly in the order listed above — but use judgement. A developer who's already implementing doesn't need you to go back and write RESEARCH.md.

## Attention

- Surface urgent signals first: failing CI, unaddressed review feedback, sessions waiting for input.
- Spread attention across the portfolio — don't tunnel-vision on one ticket while others stall.

## Quality Standards per Artifact

What "good" looks like for each authored artifact:

- **RESEARCH.md** — Captures the problem being solved, the business motivation, relevant domain context, and related tickets/history. A developer reading this should understand *why* this work matters without needing to read the Jira ticket. Also identifies the relevant repos and release branches / code versions to reference, and lists which `toolkit/wiki/` entries informed it.
- **AC.md** — Specific, testable criteria. Goes beyond Jira AC to include technical requirements, edge cases, and definition of done. Each criterion should be verifiable.
- **SPEC.md** — Design decisions, approach, affected components, class/module layout, delivery and PR plan. Explains *how* the work will be done. Should be proportional to complexity — a one-line config change doesn't need a spec. Cross-references AC.md (*what*) rather than duplicating it, and records design decisions as numbered Open items for tech-lead sign-off.
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
- PR exists, changes requested → review feedback needs addressing. Handle this before starting new work.

### What to record in ORCHESTRATOR.md

When assessing implementation, note the concrete state you observed — not just "in progress" but "3 commits on branch, last session terminated 2 hours ago after editing ClaimService.java, no PR yet." This specificity helps you (on future runs) and the developer understand exactly where things stand.

## Action Types

The orchestrator's prompt includes trust levels for each action type (auto, propose, or deny). These levels control *whether* you can act. This section defines *what* each action is, when it's appropriate, and what to include when proposing it.

### Artifact actions (low risk)

- **Write artifact** — Create or update an authored file (RESEARCH.md, AC.md, SPEC.md, etc.) in the ticket's orchestrator/ directory. When to use: whenever a concern is unmet and you can address it by writing. This is your primary output.

### Git actions (medium risk)

- **Git commit** — Commit staged changes in a ticket's worktree. When to use: after writing implementation code, fixing a bug, or addressing review feedback. Include: the commit message you'd use and which files are being committed.
- **Git push** — Push a branch to the remote. When to use: after committing, when the branch is ready for others to see (typically before PR creation or after addressing review feedback). Include: the branch name and remote.

### GitHub actions (medium-high risk)

- **PR creation** — Create a pull request from the ticket's branch. When to use: implementation is complete, tests pass, the work is ready for review. Include: target branch, PR title, description draft, and which reviewers to request.
- **PR merge** — Merge an approved pull request. When to use: PR is approved, CI is passing, no outstanding feedback. Include: the PR URL and merge method.

### Jira actions (high risk)

- **Jira comment** — Post a comment on the Jira ticket. When to use: to communicate progress, flag blockers, or summarise completed work. Include: the comment text. Keep it concise and useful to humans reading the ticket.
- **Jira transition** — Move the ticket to a different status. When to use: when the ticket's actual state clearly matches a different Jira status (e.g., work is complete, PR is merged → transition to Done). Include: the target status and evidence supporting the transition.

### Time tracking actions

- **Time log** — Log time spent on the ticket. When to use: when observable activity (session durations, commit timestamps) provides a reasonable basis for a time entry. Include: duration, description of work, and the evidence you used to estimate.

### Launching sessions

- **Launch Claude session** — Start a new Claude Code session to work on a specific task for a ticket. When to use: when implementation needs to start or continue, review feedback needs addressing, or a specific technical task needs doing. Include: the working directory, a specific prompt that references the relevant artifacts (AC.md, review comments, etc.), and what the session should accomplish.

## Proposing Actions

When your trust level for an action is "propose":
- Write the proposal to `{ticket}/orchestrator/actions.yaml` (ticket-scoped) or `.duct/actions.yaml` (cross-cutting) using the shapes in the Agents / Actions sections
- Include in `description` what specifically you're proposing, and in `detail.prompt` the evidence (sync snapshots, artifact state) and any context the developer needs to approve or reject it
- One action per intent — don't bundle unrelated actions
- Do not re-emit an action the developer has already rejected unless circumstances have materially changed (and explain what changed)

When your trust level is "auto", execute the action directly. When it's "deny", do not attempt or emit it.

Be conservative with external actions. When uncertain, write observations to ORCHESTRATOR.md instead of emitting an action.

## Agents

Reusable session prompts live under `toolkit/agents/`. Each agent is a markdown file with YAML frontmatter (`name`, optional `description`) whose body becomes the session prompt. Agents exist so developers and the orchestrator can reach for a known-good prompt instead of composing a new one each time.

This section is the authoritative source for **when** to propose each agent. The agent body describes *how* it does its work; WORKFLOW.md describes *when* it should run and what should already be in place. When an agent fits an unmet concern, prefer it over a free-form prompt.

Action files use a top-level `actions:` key with a list of entries. Append new entries to the existing list; don't overwrite. Surface agents as pending actions with this shape:

```yaml
actions:
  - id: <uuid>
    type: prompt
    description: Research the ticket using the research agent
    status: pending
    detail:
      agent: research
      ticket: PS-1234
    created_at: "<iso-8601>"
```

`detail.agent` is a name-by-reference to `toolkit/agents/<name>.md`. The body is resolved at approval time, so later edits to the agent file take effect.

When no existing agent fits — e.g. an implementation brief that references specific files and methods — emit a free-form prompt in the same file:

```yaml
actions:
  - id: <uuid>
    type: prompt
    description: <short, factual one-liner>
    status: pending
    detail:
      prompt: |
        <self-contained brief: working directory, files and artifacts to
         reference (AC.md, ORCHESTRATOR.md, specific source files), and
         what the session should accomplish.>
      ticket: <TICKET-KEY>
    created_at: "<iso-8601>"
```

The filesystem is the source of truth. List `toolkit/agents/*.md` to discover what is actually available in this workspace. Don't name an agent that doesn't exist; use a free-form prompt instead.

### `workspace`

**Produces:** repo worktrees under the ticket directory — one per in-scope repo, on an auto-generated feature branch cut from the appropriate release base. No artifact file; the agent reports results in its final message.

**Propose when:** the ticket needs implementation or research work and its directory has no repo worktrees yet, or is missing a repo that research/spec/implementation will clearly need.

**Do not propose when:** all likely-relevant repos already have worktrees under the ticket directory, or the ticket's work is entirely in `orchestrator/` artifacts (pure spec/research tickets with no code change).

**Preconditions:**
- `orchestrator/TICKET.md` exists (synced).
- `repoPaths` is configured in the workspace config.
- Runs **before** `research` — do not wait for RESEARCH.md.

**After it runs:** the Workspace concern is addressed. `research` can proceed against live worktrees; `spec` and implementation can reference real file paths.

### `research`

**Produces:** `orchestrator/RESEARCH.md`, and contributes any durable lessons to the workspace `toolkit/wiki/` via the `wiki-contributor` subagent.

**Propose when:** the ticket has no RESEARCH.md and is either a feature ticket or a non-trivial bug.

**Do not propose when:** the ticket is a small, well-understood bug where the domain context is already clear from TICKET.md alone. Research adds cost; skip it when the payoff is thin.

**Preconditions:** `workspace` has normally run first so research can grep live worktrees. If worktrees are not yet set up, propose `workspace` first.

**After it runs:** the Context concern is addressed. The ticket is ready for AC work (if applicable) or — for simple bugs — direct implementation.

### `acceptance-criteria`

**Produces:** `orchestrator/AC.md` with each Jira AC disambiguated and verification methods specified per criterion.

**Propose when:** the Jira ticket has client-agreed acceptance criteria listed, `orchestrator/AC.md` is missing (or demonstrably out of date with the ticket), and the Context concern has been addressed.

**Do not propose when:** the Jira ticket has no client-agreed AC listed. The agent cannot invent criteria; follow up with the BA to get AC agreed, or fall back to a free-form session that drafts AC from first principles if that is appropriate for the ticket.

**Preconditions:**
- Jira ticket has client-agreed acceptance criteria listed.
- `orchestrator/RESEARCH.md` exists (propose `research` first if not — AC disambiguation without domain context tends to miss nuance).

**After it runs:** the Acceptance criteria concern is addressed. The ticket is ready for spec / workspace / implementation concerns.

### `spec`

**Produces:** `orchestrator/SPEC.md` — technical implementation plan covering scope, repos/branches/coordinates, module or class layout, migrations, verification layering, delivery/PR plan, and open design decisions.

**Propose when:** implementation work is about to start, `orchestrator/SPEC.md` is missing, and the change is non-trivial (more than a single-line config change, rename, or log tweak).

**Do not propose when:** the change is genuinely trivial. If you propose it anyway, the agent does its own triviality check and will stop and ask for confirmation before writing. Err on the side of proposing when in doubt — triviality is a high bar.

**Preconditions:**
- `orchestrator/RESEARCH.md` should exist (propose `research` first if not).
- If the Jira ticket has acceptance criteria listed, `orchestrator/AC.md` must exist (propose `acceptance-criteria` first if not). If the ticket has no AC, the agent runs on TICKET + RESEARCH alone.

**After it runs:** the Specification concern is addressed. Any numbered Open items (D-/S-numbered) are raised for tech-lead sign-off before implementation locks in.

### `wiki-reader`

**Produces:** no artifact; returns a curated briefing (~300 words) of relevant lessons / conventions / domain notes / env quirks from the workspace wiki for whichever session invoked it.

**Propose when:** almost never — the orchestrator does not normally propose this. Sessions invoke it implicitly via the CLAUDE.md instruction whenever they begin substantive work on a ticket. Direct `prompt` actions only make sense for debugging.

**Preconditions:** none. Behaves correctly on an empty wiki (returns "(no relevant wiki context)").

**After it runs:** the calling session has a brief on what the wiki already knows about the area it's about to work in.

### `wiki-contributor`

**Produces:** zero or more `toolkit/wiki/<name>.md` entries plus an updated `toolkit/wiki/INDEX.md` row per entry, captured eagerly from the calling session's exchange.

**Propose when:** almost never — sessions invoke it implicitly when corrected, when addressing PR comments, when given non-obvious opening context, when fixing build/test/env issues, and as an end-of-task pass. The orchestrator does not need to propose it.

**Do not propose when:** there is no active session to evaluate. The contributor needs a live conversation or transcript.

**Preconditions:** none. The contributor reads `toolkit/wiki/INDEX.md` itself before writing.

**After it runs:** new lessons are captured in `toolkit/wiki/`. Most invocations write at least one entry.

### `wiki-maintainer`

**Produces:** dedupe / prune / consolidate of `toolkit/wiki/`, with a rebuilt `INDEX.md` alphabetised within type sections.

**Propose when:** weekly cadence (no maintainer run in the last 7 days), OR on-demand when the wiki has >50 entries or `INDEX.md` exceeds 200 lines, OR when the contributor reports an oversized index in its final message.

**Do not propose when:** the wiki has fewer than ~10 entries — there is nothing meaningful to consolidate.

**Preconditions:** the wiki exists and has multiple entries.

**After it runs:** duplicates are merged, stale entries are dropped, the index is tight again.

## Workspace wiki

The `toolkit/wiki/` folder is a curated knowledge base shared across all sessions. It captures four kinds of entry: **lessons** (mistakes corrected during sessions), **conventions** (project patterns surfaced during work), **domain knowledge** (what fields mean, why a workflow exists), and **environment quirks** (build, sandbox, tooling gotchas). It is the durable counterpart to per-ticket `orchestrator/RESEARCH.md` and replaces the older `research/` folder concept.

The wiki is curated by three Claude Code subagents shipped with duct: `wiki-reader`, `wiki-contributor`, `wiki-maintainer`. Sessions invoke them implicitly via the per-ticket and workspace-root `CLAUDE.md` instructions — there are no hooks. Direct invocation (`Task` tool, `subagent_type: "wiki-reader"`) is the supported entry point.

**Format.** Each entry is `toolkit/wiki/<name>.md` with frontmatter `name`, `type` (one of `lesson` / `convention` / `domain` / `env`), `description`, optional `tags`. Body sections: **Rule**, **Why**, **How to apply**.

**INDEX.md.** Single agent-facing TOC kept under 200 lines so it always fits in context. The contributor appends rows when it writes; the maintainer rebuilds it on review.

**Disposition.** The contributor captures eagerly — when in doubt, write the entry. The maintainer is responsible for dedup and pruning. This split lets the wiki accumulate without becoming noisy.

## Working Notes

Always update ORCHESTRATOR.md after evaluating a ticket. Record:
- What you observed (artifact state, sync snapshot signals, blockers)
- What action you took or proposed
- What you chose not to do and why

This gives you continuity across runs and gives the developer transparency into your reasoning.

## Agent Prompts

Specific workflow tasks (writing background documents, drafting acceptance criteria, designing specifications, reviewing code) will have dedicated agent prompt templates that provide detailed, structured guidance. These are being developed separately. In the meantime, the orchestrator should write specific, contextual prompts when launching sessions — referencing the relevant artifacts (AC.md, review comments, SPEC.md) and clearly describing what the session should accomplish.
