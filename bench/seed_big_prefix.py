#!/usr/bin/env python3
"""Seed a synthetic conda prefix with N fake PrefixRecord JSON files.

Used to exercise Track B suspects S1 (quadratic diff sort), S2 (PrefixGraph
O(N^2) init), S3 (History.update on long history), S5 (clobber check over
large prefix), without requiring the machinery to have actually installed
50k real packages.

Usage:
    python bench/seed_big_prefix.py --name bench_big --records 50000

    # Or with simple bare-name deps (pre-2026-04-24 behaviour):
    python bench/seed_big_prefix.py --name bench_big --records 50000 --simple-deps

The resulting prefix is real enough that `conda update --all --dry-run`
will load its conda-meta and hit the diff, PrefixGraph, and History code
paths. It is not installable-from (the "files" fields are empty), which is
fine because --dry-run never reads them.

Default dep shape (as of 2026-04-24) mimics conda-forge's observed
distribution: exponential fan-out with mean ~2.5, long tail to ~20,
version-constrained deps on ~40 % of dep lines, varied build strings
and subdirs. Pass ``--simple-deps`` for the old bare-name fixture that
S1/S2/S11 were originally written against (still useful for baseline
comparisons).

This script is safe to rerun: it wipes the prefix's conda-meta first.
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from pathlib import Path

TEMPLATE_RECORD = {
    "build": "py313_0",
    "build_number": 0,
    "channel": "https://conda.anaconda.org/tempo-synthetic",
    "constrains": [],
    "depends": [],
    "files": [],
    "md5": "0" * 32,
    "paths_data": {"paths": [], "paths_version": 1},
    "platform": None,
    "arch": None,
    "sha256": "0" * 64,
    "size": 0,
    "subdir": "noarch",
    "url": "",
    "version": "0.0.0",
}

# Pools for the realistic dep generator. Kept here (not in
# bench/phase2/fixtures.py) so ``seed_big_prefix.py`` stays
# self-contained for the CLI/hyperfine driver.
_VERSION_POOL = [
    "0.1.0",
    "0.5.2",
    "1.0.0",
    "1.2.3",
    "1.4.1",
    "2.0.0",
    "2.1.0rc1",
    "3.0.0",
    "3.13",
    "4.5.6",
]
_BUILD_POOL = [
    "py313_0",
    "py313_1",
    "py312_0",
    "h0a0a0a0_0",
    "h1b1b1b1_0",
    "pyhd8ed1ab_0",
    "hc9c84f9_0",
    "0",
]
_SUBDIR_POOL = [
    "noarch",
    "noarch",
    "noarch",  # bias towards noarch ~30 %
    "linux-64",
    "linux-64",
    "osx-arm64",
    "win-64",
    "linux-aarch64",
]


def prefix_path(name: str) -> Path | None:
    """Look up an existing env by name. Returns None if not found."""
    try:
        out = subprocess.check_output(
            ["conda", "info", "--envs", "--json"], text=True
        )
    except (OSError, subprocess.CalledProcessError) as e:
        sys.exit(f"error: cannot query conda envs: {e}")
    info = json.loads(out)
    for env_path in info.get("envs", []):
        if Path(env_path).name == name:
            return Path(env_path)
    return None


def _realistic_records(n: int, *, seed: int = 42):
    """Yield ``n`` dicts that look like real conda-meta records with
    exponential dep fan-out, version-constrained deps, and varied
    builds/subdirs. See docstring on the module for the distribution
    shape."""
    rng = random.Random(seed)
    names = [f"tempo-synthetic-pkg-{i:06d}" for i in range(n)]
    versions = [rng.choice(_VERSION_POOL) for _ in range(n)]

    def _fanout() -> int:
        return min(int(rng.expovariate(1.0 / 2.5)), 20)

    def _dep_spec(target_idx: int) -> str:
        name = names[target_idx]
        ver = versions[target_idx]
        roll = rng.random()
        if roll < 0.6:
            return name
        major = ver.split(".", 1)[0]
        if roll < 0.9:
            try:
                m = int(major)
                return f"{name} >={major}.0,<{m + 1}.0"
            except ValueError:
                return f"{name} >={ver}"
        return f"{name} {ver}"

    for i in range(n):
        k = min(_fanout(), i)
        dep_indices = rng.sample(range(i), k) if k else []
        build = rng.choice(_BUILD_POOL)
        subdir = rng.choice(_SUBDIR_POOL)
        platform = None if subdir == "noarch" else subdir.split("-")[0]
        yield {
            **TEMPLATE_RECORD,
            "name": names[i],
            "version": versions[i],
            "build": build,
            "subdir": subdir,
            "platform": platform,
            "depends": [_dep_spec(j) for j in dep_indices],
            "fn": f"{names[i]}-{versions[i]}-{build}.conda",
        }


def _simple_records(n: int):
    """Pre-2026-04-24 fixture: bare-name deps, all records identical
    shape except for name. Kept for regression comparisons."""
    for i in range(n):
        name = f"tempo-synthetic-pkg-{i:06d}"
        yield {
            **TEMPLATE_RECORD,
            "name": name,
            "version": "0.0.0",
            "build": "py313_0",
            "fn": f"{name}-0.0.0-py313_0.conda",
        }


def seed(prefix: Path, n: int, *, simple_deps: bool = False) -> None:
    meta = prefix / "conda-meta"
    if meta.exists():
        for p in meta.glob("*.json"):
            p.unlink()
    meta.mkdir(parents=True, exist_ok=True)

    history_lines = [
        "==> 2024-01-01 00:00:00 <==",
        "# cmd: conda create -n bench_big",
        "# conda version: 0.0.0",
    ]

    generator = _simple_records(n) if simple_deps else _realistic_records(n)
    for record in generator:
        name = record["name"]
        version = record["version"]
        build = record["build"]
        (meta / f"{name}-{version}-{build}.json").write_text(json.dumps(record))
        history_lines.append(f"+{name}-{version}-{build}")

    (meta / "history").write_text("\n".join(history_lines) + "\n")
    shape = "simple-deps" if simple_deps else "realistic"
    print(f"wrote {n} synthetic records ({shape}) to {meta}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="bench_big")
    ap.add_argument("--records", type=int, default=50000)
    ap.add_argument(
        "--simple-deps",
        action="store_true",
        help="emit bare-name deps (pre-2026-04-24 fixture)",
    )
    ns = ap.parse_args()

    prefix = prefix_path(ns.name)
    if prefix is None:
        subprocess.check_call(
            ["conda", "create", "-n", ns.name, "-y", "--no-default-packages"]
        )
        prefix = prefix_path(ns.name)
        if prefix is None:
            sys.exit(f"error: created env {ns.name!r} but cannot locate its prefix")
    seed(prefix, ns.records, simple_deps=ns.simple_deps)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
