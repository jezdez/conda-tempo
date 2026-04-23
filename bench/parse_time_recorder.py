#!/usr/bin/env python3
"""Extract conda's internal `time_recorder` per-phase timings.

conda ships a `time_recorder` decorator (conda.common.io.time_recorder) that
records phase durations into a module-level dict. This script runs a conda
command with CONDA_INSTRUMENTATION_ENABLED=1, then dumps the recorded
timings to data/phase1/<workload>/time_recorder.json.

Usage:
    python bench/parse_time_recorder.py <workload> -- <conda-args...>

Notes:
    - Requires a conda build where `time_recorder` is active. As of conda
      26.x this is the default when the env var is set.
    - The recorder aggregates across all calls within a process, so the
      output is the total time spent in each marker, not per-invocation.
"""
from __future__ import annotations

import argparse
import json
import os
import runpy
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA = REPO_ROOT / "data" / "phase1"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("workload")
    ap.add_argument("args", nargs=argparse.REMAINDER)
    ns = ap.parse_args()

    conda_args = [a for a in ns.args if a != "--"]
    if not conda_args:
        ap.error("missing conda args after --")

    os.environ["CONDA_INSTRUMENTATION_ENABLED"] = "1"
    sys.argv = ["conda", *conda_args]
    try:
        runpy.run_module("conda.cli", run_name="__main__", alter_sys=True)
    except SystemExit:
        pass

    try:
        from conda.common.io import _CHRONOS_COLLECTED_FNS
    except ImportError:
        try:
            from conda.common.io import time_recorder_statistics
            collected = time_recorder_statistics()
        except ImportError:
            print(
                "error: could not locate time_recorder collection on this conda version",
                file=sys.stderr,
            )
            return 2
    else:
        collected = dict(_CHRONOS_COLLECTED_FNS)

    out_dir = DATA / ns.workload
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "time_recorder.json"

    def default(o):
        if hasattr(o, "__dict__"):
            return o.__dict__
        return str(o)

    out_path.write_text(json.dumps(collected, indent=2, default=default))
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
