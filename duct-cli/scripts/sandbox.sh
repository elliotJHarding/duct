#!/usr/bin/env bash
# Hermetic duct sandbox. Two ways to use it:
#
#   source duct-cli/scripts/sandbox.sh
#     Rewires HOME / XDG / JIRA / GH env in your CURRENT shell. Then run
#     `duct` as many times as you like — nothing touches your real ~.
#     Call `duct-sandbox-exit` to restore.
#
#   duct-cli/scripts/sandbox.sh [args...]
#     One-shot. Runs `duct args...` with the sandbox env and exits.
#
# Flags (both modes):
#   --reset   wipe the sandbox directory
#   --where   print the sandbox path
#
# Override the location via DUCT_SANDBOX_HOME (defaults to
# ~/.cache/duct-sandbox).

# Detect sourced vs executed. Bash and zsh both support `return` from a
# sourced file but error when running as a script.
if (return 0 2>/dev/null); then
  _duct_sandbox_sourced=1
else
  _duct_sandbox_sourced=0
fi

# Use ${BASH_SOURCE[0]:-${(%):-%x}} to locate the script in either shell;
# fall back to $0 when both fail. The path itself isn't used except by
# --reset, but keep it for diagnostics.
_duct_sandbox_dir="${DUCT_SANDBOX_HOME:-$HOME/.cache/duct-sandbox}"

_duct_sandbox_quit() {
  # Exit script-mode, return sourced-mode. Status comes from $1 (default 0).
  if [[ "$_duct_sandbox_sourced" -eq 1 ]]; then
    return "${1:-0}"
  else
    exit "${1:-0}"
  fi
}

# Flag handling — works in both modes.
case "${1:-}" in
  --reset)
    if [[ -d "$_duct_sandbox_dir" ]]; then
      echo "Removing $_duct_sandbox_dir"
      rm -rf "$_duct_sandbox_dir"
    else
      echo "Nothing to remove at $_duct_sandbox_dir"
    fi
    _duct_sandbox_quit 0
    ;;
  --where)
    echo "$_duct_sandbox_dir"
    _duct_sandbox_quit 0
    ;;
esac

_duct_sandbox_activate() {
  mkdir -p "$_duct_sandbox_dir" || return 1

  # Snapshot originals once, so a double-source doesn't lose them.
  if [[ -z "${_DUCT_SANDBOX_ACTIVE:-}" ]]; then
    _DUCT_SANDBOX_ORIG_HOME="$HOME"
    _DUCT_SANDBOX_ORIG_XDG_CONFIG_HOME="${XDG_CONFIG_HOME-__unset__}"
    _DUCT_SANDBOX_ORIG_XDG_DATA_HOME="${XDG_DATA_HOME-__unset__}"
    _DUCT_SANDBOX_ORIG_XDG_CACHE_HOME="${XDG_CACHE_HOME-__unset__}"
    _DUCT_SANDBOX_ORIG_JIRA_EMAIL="${JIRA_EMAIL-__unset__}"
    _DUCT_SANDBOX_ORIG_JIRA_TOKEN="${JIRA_TOKEN-__unset__}"
    _DUCT_SANDBOX_ORIG_GH_TOKEN="${GH_TOKEN-__unset__}"
    _DUCT_SANDBOX_ORIG_GITHUB_TOKEN="${GITHUB_TOKEN-__unset__}"
    _DUCT_SANDBOX_ORIG_PS1="${PS1-__unset__}"
  fi

  export HOME="$_duct_sandbox_dir"
  export XDG_CONFIG_HOME="$_duct_sandbox_dir/.config"
  export XDG_DATA_HOME="$_duct_sandbox_dir/.local/share"
  export XDG_CACHE_HOME="$_duct_sandbox_dir/.cache"
  unset JIRA_EMAIL JIRA_TOKEN GH_TOKEN GITHUB_TOKEN

  export _DUCT_SANDBOX_ACTIVE="$_duct_sandbox_dir"
}

duct-sandbox-exit() {
  if [[ -z "${_DUCT_SANDBOX_ACTIVE:-}" ]]; then
    echo "Not in a duct sandbox."
    return 1
  fi

  _duct_sandbox_restore() {
    # $1=var name, $2=saved value. "__unset__" means it wasn't set originally.
    if [[ "$2" == "__unset__" ]]; then
      unset "$1"
    else
      export "$1=$2"
    fi
  }
  export HOME="$_DUCT_SANDBOX_ORIG_HOME"
  _duct_sandbox_restore XDG_CONFIG_HOME "$_DUCT_SANDBOX_ORIG_XDG_CONFIG_HOME"
  _duct_sandbox_restore XDG_DATA_HOME   "$_DUCT_SANDBOX_ORIG_XDG_DATA_HOME"
  _duct_sandbox_restore XDG_CACHE_HOME  "$_DUCT_SANDBOX_ORIG_XDG_CACHE_HOME"
  _duct_sandbox_restore JIRA_EMAIL      "$_DUCT_SANDBOX_ORIG_JIRA_EMAIL"
  _duct_sandbox_restore JIRA_TOKEN      "$_DUCT_SANDBOX_ORIG_JIRA_TOKEN"
  _duct_sandbox_restore GH_TOKEN        "$_DUCT_SANDBOX_ORIG_GH_TOKEN"
  _duct_sandbox_restore GITHUB_TOKEN    "$_DUCT_SANDBOX_ORIG_GITHUB_TOKEN"
  if [[ "$_DUCT_SANDBOX_ORIG_PS1" != "__unset__" ]]; then
    PS1="$_DUCT_SANDBOX_ORIG_PS1"
  fi

  unset _DUCT_SANDBOX_ACTIVE \
        _DUCT_SANDBOX_ORIG_HOME _DUCT_SANDBOX_ORIG_PS1 \
        _DUCT_SANDBOX_ORIG_XDG_CONFIG_HOME _DUCT_SANDBOX_ORIG_XDG_DATA_HOME _DUCT_SANDBOX_ORIG_XDG_CACHE_HOME \
        _DUCT_SANDBOX_ORIG_JIRA_EMAIL _DUCT_SANDBOX_ORIG_JIRA_TOKEN \
        _DUCT_SANDBOX_ORIG_GH_TOKEN _DUCT_SANDBOX_ORIG_GITHUB_TOKEN
  unset -f _duct_sandbox_restore
  echo "Exited duct sandbox."
}

_duct_sandbox_activate || _duct_sandbox_quit 1

cat <<EOF
Sandbox active.
  HOME -> $HOME
  duct -> $(command -v duct 2>/dev/null || echo "(not on PATH)")
  exit -> duct-sandbox-exit
  reset -> $0 --reset

Your real ~ is untouched. ~/.config/duct/, ~/workspace/, ~/.zshrc all
resolve under the sandbox path above.
EOF

if [[ "$_duct_sandbox_sourced" -eq 0 ]]; then
  exec duct "$@"
else
  PS1="(duct-sandbox) ${PS1-}"
fi
