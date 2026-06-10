#!/usr/bin/env bash
# Run duct against a fully isolated sandbox — nothing touches the real setup.
#
# Isolation, piece by piece:
#   DUCT_STATE_DIR          → global state (state.yaml pointer, focus, logs)
#   PYTHON_KEYRING_BACKEND  → credentials go to a plaintext file instead of
#                             the macOS Keychain (needs `pipx inject duct
#                             keyrings.alt`, one-time)
#   XDG_DATA_HOME           → puts that credential file inside the sandbox
#   DUCT_DEFAULT_WORKSPACE  → setup's suggested workspace path lands in the
#                             sandbox even if you just press Enter
#   JIRA_* env unset        → the legacy-credential migration can't copy your
#                             real shell-exported token into the sandbox
#
# Deliberately NOT isolated: the gh CLI token (read-only; lets the GitHub
# phase show real orgs/PR data). Don't install the daemon from inside the
# sandbox — it would repoint launchd at the sandbox workspace.
#
# Usage:
#   scripts/duct-sandbox.sh                 # duct setup in the sandbox
#   scripts/duct-sandbox.sh status          # any duct command
#   scripts/duct-sandbox.sh --reset         # wipe the sandbox and re-setup
#
# The sandbox lives at ~/duct-sandbox; delete that directory to clean up.

set -euo pipefail

SANDBOX="${DUCT_SANDBOX_DIR:-$HOME/duct-sandbox}"

if [[ "${1:-}" == "--reset" ]]; then
    rm -rf "$SANDBOX"
    shift
fi
mkdir -p "$SANDBOX"

if ! "$HOME/.local/pipx/venvs/duct/bin/python" -c "import keyrings.alt" 2>/dev/null; then
    echo "Installing the file-based keyring backend into the duct venv…"
    pipx inject duct keyrings.alt
fi

echo "Sandbox: $SANDBOX (real setup untouched)"
env -u JIRA_EMAIL -u JIRA_TOKEN \
    DUCT_STATE_DIR="$SANDBOX/state" \
    DUCT_DEFAULT_WORKSPACE="$SANDBOX/workspace" \
    XDG_DATA_HOME="$SANDBOX/share" \
    PYTHON_KEYRING_BACKEND=keyrings.alt.file.PlaintextKeyring \
    duct "${@:-setup}"
