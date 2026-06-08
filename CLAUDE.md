# duct repo

## Rebuild duct-tui after any code change

There are **two** pipx installs that need rebuilding after a code change:

1. **`duct-tui`** — the TUI app, with `duct-cli` injected as a dependency.
2. **`duct`** — the standalone CLI used for `duct sync`, `duct doctor`, etc. This venv has its own copy of `duct-cli` that is **not** affected by `pipx inject` into duct-tui.

Run all three after every code change before declaring a task done:

```bash
pipx install --force /Users/hardinge/workspace/workflow/duct/duct-cli
pipx install --force /Users/hardinge/workspace/workflow/duct/duct-tui
pipx inject  --force duct-tui /Users/hardinge/workspace/workflow/duct/duct-cli
```

Why all three:
- `install --force duct-cli` rebuilds the standalone `duct` CLI venv. Skip this and `duct sync` from the shell still runs the old code, even though the TUI is fresh.
- `install --force duct-tui` rebuilds the duct-tui package into its isolated venv.
- `inject --force duct-tui duct-cli` reinstalls the `duct` core library into the duct-tui venv. Without it, the newly-built TUI runs against the old `duct-cli` snapshot.

After reinstalling, **restart the running TUI** — Python module caches in the live process keep the old code in memory until the process exits.

The local `duct-tui/.venv/` is for editable-mode development and tests only — the user does not run the app from it.
