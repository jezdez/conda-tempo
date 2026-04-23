#!/usr/bin/env python3
"""Run a Phase 2 benchmark under memray and commit artifacts to data/phase2/<id>/.

Each ``bench_<id>.py`` in this directory must expose::

    def register_memray(n: int) -> None:
        \"\"\"Run the hot path once (or a few times) under the active memray tracer.\"\"\"

Usage:
    python bench/phase2/run_memray.py <suspect_id> [-n N]

Writes to data/phase2/<suspect_id>/:
    memray_n<N>.bin                 aggregated + native trace
    memray_n<N>.summary.txt         top-20 allocator table
    memray_n<N>.meta.json           peak RSS, total allocations, wall time
    memray_n<N>.flamegraph.html     HTML flamegraph

The heavy lifting is done by the ``memray.Tracker`` context manager
around the suspect's ``register_memray(n)`` call, so we only profile
the target code path — not pyperf worker startup, not argparse, not the
harness itself.
"""
from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
from pathlib import Path

import memray
from memray import FileReader, FileFormat

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA = REPO_ROOT / "data" / "phase2"


def _dump_meta(bin_path: Path, meta_path: Path) -> None:
    reader = FileReader(str(bin_path))
    md = reader.metadata
    wall_time_s = (md.end_time - md.start_time).total_seconds()
    meta = {
        "peak_memory_bytes": md.peak_memory,
        "peak_memory_human": f"{md.peak_memory / (1024 * 1024):.2f} MiB",
        "total_allocations": md.total_allocations,
        "total_frames": md.total_frames,
        "wall_time_s": wall_time_s,
        "python_allocator": str(md.python_allocator),
        "has_native_traces": md.has_native_traces,
        "command_line": md.command_line,
        "pid": md.pid,
    }
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("suspect_id")
    ap.add_argument("-n", "--records", type=int, default=5000)
    ns = ap.parse_args()

    # Ensure sibling bench_* modules import cleanly.
    sys.path.insert(0, str(Path(__file__).resolve().parent))

    module_name = f"bench_{ns.suspect_id}"
    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        sys.exit(f"error: cannot import {module_name}: {e}")

    if not hasattr(module, "register_memray"):
        sys.exit(
            f"error: {module_name} must define register_memray(n: int) -> None",
        )

    out_dir = DATA / ns.suspect_id
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"memray_n{ns.records}"
    bin_path = out_dir / f"{stem}.bin"
    summary_path = out_dir / f"{stem}.summary.txt"
    meta_path = out_dir / f"{stem}.meta.json"
    flamegraph_path = out_dir / f"{stem}.flamegraph.html"

    for p in (bin_path, summary_path, meta_path, flamegraph_path):
        if p.exists():
            p.unlink()

    # Trace only the suspect's hot path.
    with memray.Tracker(
        str(bin_path),
        native_traces=True,
        follow_fork=True,
        file_format=FileFormat.AGGREGATED_ALLOCATIONS,
    ):
        module.register_memray(ns.records)

    with summary_path.open("w") as fh:
        subprocess.check_call(
            ["memray", "summary", "-r", "20", str(bin_path)],
            stdout=fh,
        )
    _dump_meta(bin_path, meta_path)
    subprocess.check_call(
        ["memray", "flamegraph", "--force", "-o", str(flamegraph_path), str(bin_path)],
    )

    print(f"wrote {bin_path}")
    print(f"wrote {summary_path}")
    print(f"wrote {meta_path}")
    print(f"wrote {flamegraph_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
