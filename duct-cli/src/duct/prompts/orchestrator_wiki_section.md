## Maintaining the workspace wiki

The `toolkit/wiki/` folder is a curated knowledge base shared across all sessions. It captures lessons, conventions, durable domain knowledge, and environment quirks. Entries are curated by three Claude Code subagents (`wiki-reader`, `wiki-contributor`, `wiki-maintainer`) that sessions invoke implicitly via the per-ticket `CLAUDE.md` instruction — you do not edit `toolkit/wiki/` directly.

Keep WORKFLOW.md and `toolkit/wiki/` distinct:

- **WORKFLOW.md** — *how we work.* Heuristics, artifact standards, ticket-type conventions, ordering rules. Process rules.
- **toolkit/wiki/** — *what we have learned.* Lessons from corrections, project conventions, domain facts, environment quirks. Anything a future agent would want to know before starting a task.

On each run:

- Glance at `toolkit/wiki/INDEX.md` — it lists every entry by name, type, and description. A scan is enough; do not deep-read every entry.
- When you observe a durable lesson surface during ticket evaluation that isn't yet in the wiki, write a `prompt` action that calls the `wiki-contributor` subagent for the relevant session. Do not edit `toolkit/wiki/` files yourself.
- Propose `wiki-maintainer` when the wiki is large (>50 entries) or when `INDEX.md` exceeds 200 lines, or when no maintainer action has been proposed in the last ~7 days. Use a `prompt` action with `agent: wiki-maintainer` so the user can approve a maintenance pass.

Do not capture ticket-specific notes in the wiki — those belong in the ticket's `orchestrator/RESEARCH.md`.
