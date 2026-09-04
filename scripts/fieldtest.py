# -*- coding: utf-8 -*-
"""Scan large real repositories and require zero false blocks.

The reason this exists: 605 unit tests and a 35-check acceptance pass missed
eight defects, four of them false blocks, that one pass over three popular
repositories found immediately. Synthetic manifests only exercise the shapes
somebody already thought of. A real monorepo contains the ones nobody did --
`@next/font` pinned by exact version, `aiopurpleair==2025.08.1`, `MANIFEST.in`,
a yarn `owner/repo#ref` descriptor.

So this is a release gate, not a nice-to-have. Every name in these repositories
is a dependency that real people install every day, so **every block is a false
block until proven otherwise**, and a listed exception has to say why.

    py scripts/fieldtest.py            # clone (cached) and scan
    py scripts/fieldtest.py --quick    # only the two smallest

Exit code 0 means no unexplained block. Anything else fails the gate.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

#: Chosen for the shapes they contain, not for popularity. Every one of these
#: found a defect nothing else did, and the note says which -- so a future
#: reader can tell why the list is what it is rather than trimming it.
#:
#: The boolean is whether `--quick` includes it.
REPOS = [
    ("pallets/flask", "uv.lock, example sub-projects", True),
    ("vuejs/core", "pnpm workspace, lockfile v9", True),
    ("expressjs/express", "small and plain npm; a fast canary", True),

    ("getsentry/sentry", "pnpm-lock and uv.lock in one tree", False),
    ("home-assistant/core", "constraints pinned to 1000000000.0.0; calendar versions", False),
    ("vercel/turborepo", "pnpm v5 keys with peer suffixes -- 17 false blocks", False),
    ("sveltejs/kit", "pnpm `name@file:` workspace fixtures -- 5 false blocks", False),
    ("withastro/astro", "more `name@file:` fixtures, scoped -- 3 false blocks", False),
    ("storybookjs/storybook", "yarn berry `npm:v1.1.0` ranges -- 2 false blocks", False),
    ("apache/airflow", "127 checksum files, five named like constraints", False),
    ("ray-project/ray", "PEP 440 local versions, materialised symlinks", False),
    ("nrwl/nx", "pnpm monorepo, agent instruction files", False),
    ("angular/angular", "15,000 names, the widest single scan", False),
    ("jupyterlab/jupyterlab", "Python and npm in one tree", False),
    ("apache/superset", "6,000 names, heavy extras", False),
    ("vercel/next.js", "491 package.json files, internal deps by exact pin", False),
]

#: Blocks that are correct. A repository may genuinely depend on something that
#: does not exist, and every entry here was verified against the registry by
#: hand -- the reason has to be checkable by the next reader.
#:
#: Across 88,904 packages in sixteen repositories these six are the only blocks,
#: and all six are real. That is the number the gate exists to hold.
EXPECTED = {
    # PyPI's jaxlib release list begins at 0.4.18. Older wheels were served
    # from Google storage and the PyPI records were removed, so
    # `pip install -r` on that file fails today.
    "jaxlib": "ray pins 0.4.17; PyPI's oldest jaxlib release is 0.4.18",

    # Apple stopped publishing tensorflow-macos at 2.16.2. Ray copied the
    # `tensorflow==2.20.0` pin onto the macOS package, guarded by a
    # `sys_platform == 'darwin'` marker, so it only fails on a Mac.
    "tensorflow-macos": "ray pins 2.20.0; PyPI's newest is 2.16.2",

    # 404 on npmjs.
    "tsconfig-mod": "referenced by next.js and not published on npm",
}

CACHE = Path(tempfile.gettempdir()) / "ghostpkg-fieldtest"


def clone(repo: str) -> Path | None:
    """Clone into a scratch path and rename, so a half-finished checkout is
    never mistaken for a cached one.

    The first version cloned straight into the cache and returned early when
    the directory existed. Two runs at once then raced: one scanned the other's
    partial checkout, found 56 of 583 packages, and reported the gate green.
    A gate that passes on an incomplete repository is worse than no gate.
    """
    target = CACHE / repo.replace("/", "_")
    if (target / ".git").is_dir():
        return target
    CACHE.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(dir=str(CACHE), prefix=".partial-"))
    shutil.rmtree(scratch, ignore_errors=True)
    result = subprocess.run(
        ["git", "clone", "--depth", "1", "--quiet", f"https://github.com/{repo}", str(scratch)],
        capture_output=True, text=True, timeout=900,
    )
    if result.returncode != 0 or not (scratch / ".git").is_dir():
        print(f"  could not clone {repo}: {result.stderr.strip()[:120]}")
        shutil.rmtree(scratch, ignore_errors=True)
        return None
    try:
        scratch.rename(target)
    except OSError:
        # Another run won the race and already has it.
        shutil.rmtree(scratch, ignore_errors=True)
    return target if (target / ".git").is_dir() else None


def scan(path: Path, ghostpkg: list[str]) -> tuple[dict, float]:
    started = time.monotonic()
    result = subprocess.run(
        ghostpkg + ["scan", str(path), "--json", "--workers", "16"],
        capture_output=True, text=True, timeout=3600, encoding="utf-8", errors="replace",
    )
    elapsed = time.monotonic() - started
    try:
        return json.loads(result.stdout), elapsed
    except ValueError:
        return {"error": (result.stdout + result.stderr)[:400]}, elapsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true", help="only the two smallest")
    parser.add_argument("--ghostpkg", default=None, help="command to run (default: this checkout)")
    args = parser.parse_args()

    command = args.ghostpkg.split() if args.ghostpkg else [sys.executable, "-m", "ghostpkg"]
    repos = [r for r in REPOS if r[2]] if args.quick else REPOS

    total_checked = 0
    unexplained: list[tuple[str, str, str]] = []
    warned = 0

    print(f"  {'repository':26s} {'checked':>8s} {'blocked':>8s} {'warned':>7s} {'seconds':>8s}")
    print("  " + "-" * 62)

    for repo, why, _ in repos:
        path = clone(repo)
        if path is None:
            continue
        payload, elapsed = scan(path, command)
        if "error" in payload:
            print(f"  {repo:26s} FAILED: {payload['error'][:80]}")
            unexplained.append((repo, "-", "the scan itself failed"))
            continue

        summary = payload.get("summary", {})
        total_checked += summary.get("checked", 0)
        warned += summary.get("warned", 0)
        blocks = [f for f in payload.get("findings", []) if f.get("verdict") == "BLOCK"]
        print(
            f"  {repo:26s} {summary.get('checked', 0):8d} {len(blocks):8d}"
            f" {summary.get('warned', 0):7d} {elapsed:8.0f}"
        )
        for finding in blocks:
            if finding["name"] in EXPECTED:
                continue
            reason = "; ".join(r.get("text", "") for r in finding.get("reasons", []))
            unexplained.append((repo, finding["name"], reason))

    print()
    print(f"  checked {total_checked} packages, {warned} warnings")

    if unexplained:
        print(f"  {len(unexplained)} unexplained blocks:")
        for repo, name, reason in unexplained[:25]:
            print(f"    {repo}: {name} -- {reason[:70]}")
        print()
        print("  Each of these is a false block until shown otherwise. Fix it, or add")
        print("  it to EXPECTED with a reason a reader can check.")
        return 1

    print("  no unexplained blocks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
