#!/usr/bin/env python3
"""cProfile wrapper for a single conda invocation.

Usage:
    python bench/profile.py <workload> -- <conda-args...>

Example:
    python bench/profile.py w2 -- create -n prof_tmp -y python=3.13 pandas

Writes to data/phase1/<workload>/:
    cprofile.prof        raw binary, loadable via pstats / snakeviz
    cprofile.top20.txt   pstats top-20 by cumulative time
"""
from __future__ import annotations

import argparse
import cProfile
import pstats
import runpy
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA = REPO_ROOT / "data" / "phase1"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("workload", help="w1|w2|w3 (used only as output subdir)")
    ap.add_argument("args", nargs=argparse.REMAINDER, help="args for `conda`")
    ns = ap.parse_args()

    conda_args = [a for a in ns.args if a != "--"]
    if not conda_args:
        ap.error("missing conda args after --")

    out_dir = DATA / ns.workload
    out_dir.mkdir(parents=True, exist_ok=True)
    prof_path = out_dir / "cprofile.prof"
    top_path = out_dir / "cprofile.top20.txt"

    sys.argv = ["conda", *conda_args]
    profiler = cProfile.Profile()
    try:
        profiler.enable()
        try:
            runpy.run_module("conda.cli", run_name="__main__", alter_sys=True)
        except SystemExit:
            pass
    finally:
        profiler.disable()
        profiler.dump_stats(str(prof_path))

    with top_path.open("w") as fh:
        stats = pstats.Stats(profiler, stream=fh)
        stats.sort_stats("cumulative").print_stats(20)

    print(f"wrote {prof_path}")
    print(f"wrote {top_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
