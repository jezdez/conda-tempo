#!/usr/bin/env python3
"""Seed a synthetic conda prefix with N fake PrefixRecord JSON files.

Used to exercise Track B suspects S1 (quadratic diff sort), S2 (PrefixGraph
O(N^2) init), S3 (History.update on long history), S5 (clobber check over
large prefix), without requiring the machinery to have actually installed
50k real packages.

Usage:
    python bench/seed_big_prefix.py --name bench_big --records 50000

The resulting prefix is real enough that `conda update --all --dry-run`
will load its conda-meta and hit the diff, PrefixGraph, and History code
paths. It is not installable-from (the "files" fields are empty), which is
fine because --dry-run never reads them.

This script is safe to rerun: it wipes the prefix's conda-meta first.
"""
from __future__ import annotations

import argparse
import json
import os
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
    "platform": "noarch",
    "sha256": "0" * 64,
    "size": 0,
    "subdir": "noarch",
    "url": "",
    "version": "0.0.0",
}


def prefix_path(name: str) -> Path:
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
    envs_dirs = info.get("envs_dirs") or [os.path.expanduser("~/.conda/envs")]
    return Path(envs_dirs[0]) / name


def seed(prefix: Path, n: int) -> None:
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

    for i in range(n):
        name = f"tempo-synthetic-pkg-{i:06d}"
        version = "0.0.0"
        build = "py313_0"
        record = {
            **TEMPLATE_RECORD,
            "name": name,
            "version": version,
            "build": build,
            "fn": f"{name}-{version}-{build}.conda",
        }
        (meta / f"{name}-{version}-{build}.json").write_text(json.dumps(record))
        history_lines.append(f"+{name}-{version}-{build}")

    (meta / "history").write_text("\n".join(history_lines) + "\n")
    print(f"wrote {n} synthetic records to {meta}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="bench_big")
    ap.add_argument("--records", type=int, default=50000)
    ns = ap.parse_args()

    prefix = prefix_path(ns.name)
    if not prefix.exists():
        subprocess.check_call(
            ["conda", "create", "-n", ns.name, "-y", "--no-default-packages"]
        )
    seed(prefix, ns.records)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
