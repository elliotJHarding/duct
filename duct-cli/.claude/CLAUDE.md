# duct Workspace

This is a duct workspace. See WORKFLOW.md for development lifecycle guidance.

## Structure

- Each ticket has a directory named {KEY}-{slug}/ at the workspace root (flat layout)
- Ticket artifacts live in the orchestrator/ subdirectory
- Epic metadata lives in epics/ as markdown files; each ticket's orchestrator/ symlinks EPIC.md to its epic
- Files with `source: sync` frontmatter are overwritten by sync — do not edit them
