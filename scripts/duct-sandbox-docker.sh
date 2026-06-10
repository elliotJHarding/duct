#!/usr/bin/env bash
# Test duct in a throwaway Docker container — zero contact with the host
# setup (keychain, ~/.config/duct, workspace, shell rc, launchd).
#
# Usage:
#   scripts/duct-sandbox-docker.sh                  # duct setup, ephemeral
#   scripts/duct-sandbox-docker.sh --persist        # keep state in a volume
#   scripts/duct-sandbox-docker.sh duct status      # any command
#   scripts/duct-sandbox-docker.sh bash             # poke around
#   scripts/duct-sandbox-docker.sh --rebuild        # rebuild after code changes
#   scripts/duct-sandbox-docker.sh --reset          # drop the persist volume
#
# Ephemeral runs (--rm, no volume) vanish completely on exit — every run is
# a brand-new machine. With --persist, /root (workspace + state + creds file)
# lives in the `duct-sandbox-home` Docker volume across runs.
#
# Why run+commit instead of `docker build`: on this machine the container
# default DNS (192.168.5.2) doesn't resolve, the legacy builder ignores
# --network, and buildx isn't installed. `docker run --dns 8.8.8.8` works,
# so the image is assembled in a run step and committed. The same --dns flag
# is passed at wizard runtime so the live Jira/GitHub probes resolve (duct
# only needs public endpoints: *.atlassian.net, api.github.com).
#
# In-container caveats:
#   - `claude` is not installed: the tools phase shows its red ✗ (authentic),
#     and launching sessions won't work. Setup + tutorial are fully testable.
#   - gh CLI is installed but not logged in: the GitHub phase offers
#     `gh auth login` (device flow — open the printed URL on the host),
#     a pasted PAT, or skip (ctrl+s).
#   - keyring: Linux containers have no macOS Keychain / D-Bus secret
#     service, and duct silently drops credential writes when no backend
#     works — so the image pins the keyrings.alt file backend.

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE=duct-sandbox
DNS=(--dns 8.8.8.8)

build_image() {
    echo "Building $IMAGE image (run+commit)…"
    docker rm -f duct-sandbox-build >/dev/null 2>&1 || true
    docker run "${DNS[@]}" --name duct-sandbox-build \
        -v "$REPO/duct-cli":/src/duct-cli:ro \
        python:3.12-slim bash -c "
            set -e
            apt-get update
            apt-get install -y --no-install-recommends git ca-certificates gh
            rm -rf /var/lib/apt/lists/*
            pip install --no-cache-dir /src/duct-cli keyrings.alt
        "
    docker commit \
        --change 'ENV PYTHON_KEYRING_BACKEND=keyrings.alt.file.PlaintextKeyring TERM=xterm-256color COLORTERM=truecolor' \
        --change 'WORKDIR /root' \
        --change 'CMD ["duct", "setup"]' \
        duct-sandbox-build "$IMAGE" >/dev/null
    docker rm duct-sandbox-build >/dev/null
}

case "${1:-}" in
    --reset)
        docker volume rm -f duct-sandbox-home >/dev/null
        echo "Dropped duct-sandbox-home volume."
        exit 0
        ;;
    --rebuild)
        build_image
        shift
        ;;
esac

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    build_image
fi

VOLUME_ARGS=()
if [[ "${1:-}" == "--persist" ]]; then
    VOLUME_ARGS=(-v duct-sandbox-home:/root)
    shift
fi

if [[ $# -eq 0 ]]; then
    set -- duct setup
fi
# ${arr[@]+...} guards the empty-array case: bash 3.2 (macOS default) treats
# expanding an empty array under `set -u` as an unbound-variable error.
exec docker run --rm -it "${DNS[@]}" ${VOLUME_ARGS[@]+"${VOLUME_ARGS[@]}"} "$IMAGE" "$@"
