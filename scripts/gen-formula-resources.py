#!/usr/bin/env python3
"""Generate Homebrew `resource` stanzas for the duct formula.

Resolves the full dependency closure of duct-cli + duct-tui (plus the hatchling
build stack) in a throwaway virtualenv, then queries the PyPI JSON API for each
package's sdist URL + sha256. Pillow is a C-extension, so it is emitted as a
prebuilt wheel matching the venv's CPython ABI / macOS arm64 instead of an sdist
(Homebrew forces `--no-binary=:all:`, which would otherwise build Pillow from
source and drag in jpeg-turbo/libtiff/etc.).

Re-run this each release to refresh versions + hashes, then paste the output
into Formula/duct.rb.

Usage:
    python3 scripts/gen-formula-resources.py [--python /opt/homebrew/opt/python@3.14/bin/python3.14]
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Packages provided by this repo (installed from the source tree, not pinned).
LOCAL_PACKAGES = {"duct", "duct-tui"}

# Installed alongside the runtime deps so the hatchling build backend is present
# in the venv — lets the formula install the local packages with
# `--no-build-isolation` (no PyPI fetch at build time).
BUILD_BACKEND = "hatchling"


def run(cmd: list[str]) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def resolve_closure(python: str) -> tuple[list[tuple[str, str]], tuple[int, int]]:
    """Install the packages in a temp venv and return (name, version) pairs.

    Resolution mirrors Homebrew's `--uploaded-prior-to=<now-24h>` supply-chain
    delay (see Formula#std_pip_args) so we never pin a version brew will refuse
    to install. We use a 24h cutoff: anything pip picks here is already older
    than that, so it stays older than brew's (later) install-time cutoff too.
    """
    cutoff = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=1)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    with tempfile.TemporaryDirectory() as tmp:
        venv = Path(tmp) / "venv"
        run([python, "-m", "venv", str(venv)])
        pip = str(venv / "bin" / "pip")
        run([pip, "install", "--upgrade", "pip"])
        run([
            pip, "install", f"--uploaded-prior-to={cutoff}",
            BUILD_BACKEND,
            str(REPO_ROOT / "duct-cli"),
            str(REPO_ROOT / "duct-tui"),
        ])
        listing = json.loads(run([pip, "list", "--format=json"]))
        py = json.loads(run([
            str(venv / "bin" / "python"), "-c",
            "import sys,json;print(json.dumps(list(sys.version_info[:2])))",
        ]))
    pkgs = [
        (p["name"], p["version"])
        for p in listing
        if p["name"].lower() not in LOCAL_PACKAGES and p["name"].lower() != "pip"
    ]
    return sorted(pkgs, key=lambda x: x[0].lower()), (py[0], py[1])


def pypi_release(name: str, version: str) -> list[dict]:
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    with urllib.request.urlopen(url) as resp:
        return json.load(resp)["urls"]


def pick_sdist(files: list[dict], name: str) -> dict:
    for f in files:
        if f["packagetype"] == "sdist":
            return f
    raise SystemExit(f"no sdist found for {name}")


def pick_macos_arm64_wheel(files: list[dict], name: str, pyver: tuple[int, int]) -> dict:
    tag = f"cp{pyver[0]}{pyver[1]}"
    for f in files:
        fn = f["filename"]
        if f["packagetype"] == "bdist_wheel" and tag in fn and "macosx" in fn and "arm64" in fn:
            return f
    raise SystemExit(f"no {tag} macos arm64 wheel found for {name}")


def normalize(name: str) -> str:
    """PEP 503 normalization, which is what `brew audit` expects resource
    names to match (lowercase, runs of -_. collapsed to a single dash)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def resource_block(name: str, file: dict) -> str:
    return (
        f'  resource "{normalize(name)}" do\n'
        f'    url "{file["url"]}"\n'
        f'    sha256 "{file["digests"]["sha256"]}"\n'
        f"  end"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", default=sys.executable,
                    help="Interpreter to build the closure with (use brew's python@3.14).")
    args = ap.parse_args()

    pkgs, pyver = resolve_closure(args.python)
    print(f"# Generated for CPython {pyver[0]}.{pyver[1]} / macOS arm64", file=sys.stderr)

    blocks = []
    for name, version in pkgs:
        files = pypi_release(name, version)
        if name.lower() == "pillow":
            chosen = pick_macos_arm64_wheel(files, name, pyver)
        else:
            chosen = pick_sdist(files, name)
        blocks.append(resource_block(name, chosen))
        print(f"  resolved {name} {version} -> {chosen['filename']}", file=sys.stderr)

    print("\n\n".join(blocks))


if __name__ == "__main__":
    main()
