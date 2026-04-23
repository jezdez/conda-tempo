#!/usr/bin/env python3
"""Extract conda's internal `time_recorder` per-phase timings.

conda ships a `time_recorder` decorator (conda.common.io.time_recorder) that,
when ``CONDA_INSTRUMENTATION_ENABLED=1`` is set, records one line per call
into ``~/.conda/instrumentation-record.csv`` and accumulates totals on the
class itself (``total_run_time``, ``total_call_num``).

This script:
  1. clears the instrumentation CSV,
  2. runs the conda command in-process with instrumentation enabled,
  3. dumps both the per-marker totals (from the class vars) and the raw
     per-call list (from the CSV) to
     ``data/phase1/<workload>/time_recorder.json``.

Usage:
    python bench/parse_time_recorder.py <workload> -- <conda-args...>
"""
from __future__ import annotations

import argparse
import json
import os
import runpy
import sys
from collections import defaultdict
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

    # Enable instrumentation *before* importing conda modules so the
    # CSV-append path in time_recorder.__exit__ is taken for every call.
    os.environ["CONDA_INSTRUMENTATION_ENABLED"] = "1"

    # Import now to locate the CSV file, then truncate it so we only capture
    # this invocation's samples.
    from conda.common.io import get_instrumentation_record_file, time_recorder

    record_file = Path(get_instrumentation_record_file()).expanduser()
    record_file.parent.mkdir(parents=True, exist_ok=True)
    record_file.write_text("")  # clear

    # Reset class-level accumulators in case the module has been imported
    # before (e.g. by a prior pytest run in the same interpreter).
    time_recorder.total_run_time.clear()
    time_recorder.total_call_num.clear()

    sys.argv = ["conda", *conda_args]
    try:
        runpy.run_module("conda", run_name="__main__", alter_sys=True)
    except SystemExit:
        pass

    # Parse the CSV for raw per-call samples (the class totals are already
    # aggregated).
    raw = defaultdict(list)
    if record_file.is_file():
        with record_file.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                entry_name, sample = line.split(",", 1)
                raw[entry_name].append(float(sample))

    markers = {}
    for name, total in time_recorder.total_run_time.items():
        samples = raw.get(name, [])
        markers[name] = {
            "total_seconds": total,
            "call_count": time_recorder.total_call_num.get(name, len(samples)),
            "samples_seconds": samples,
        }

    out_dir = DATA / ns.workload
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "time_recorder.json"
    out_path.write_text(json.dumps(markers, indent=2, sort_keys=True))
    print(f"wrote {out_path} ({len(markers)} markers)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
