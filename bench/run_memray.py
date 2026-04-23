#!/usr/bin/env python3
"""memray wrapper for a single conda invocation.

Usage:
    python bench/run_memray.py <workload> -- <conda-args...>

Example:
    python bench/run_memray.py w2 -- create -n prof_tmp -y python=3.13 pandas

Writes to data/phase1/<workload>/:
    memray.bin                 aggregated + native trace (committable, < ~10 MB)
    memray.summary.txt         top-20 allocators table (memray summary)
    memray.meta.json           peak memory, total allocations, fork count
    memray.flamegraph.html     rendered flamegraph, openable in any browser

Uses ``--aggregate`` so the committed .bin is small enough to track in git
(without aggregation, a W2 run produces a ~1 GB trace). Note: aggregated
captures are incompatible with ``memray stats`` — use ``summary`` or the
Python ``FileReader`` API for quantitative data.

Uses ``--follow-fork`` to capture allocations in subprocesses — important
for W2 which spawns ~190 ``compileall`` subprocesses during .pyc compile.

Uses ``--native`` for C-extension stack unwinding. On Linux-glibc this
resolves symbols inside libmambapy, libarchive, and CPython's allocator;
on macOS the native stacks are partial (dyld interposition limits) but
Python-level allocation tracking is unaffected.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA = REPO_ROOT / "data" / "phase1"


def _dump_meta(bin_path: Path, meta_path: Path) -> None:
    """Extract top-line metrics from the aggregated trace via the Python API."""
    from memray import FileReader

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
    ap.add_argument("workload", help="w1|w2|w3 (used only as output subdir)")
    ap.add_argument("args", nargs=argparse.REMAINDER, help="args for `conda`")
    ns = ap.parse_args()

    conda_args = [a for a in ns.args if a != "--"]
    if not conda_args:
        ap.error("missing conda args after --")

    out_dir = DATA / ns.workload
    out_dir.mkdir(parents=True, exist_ok=True)
    bin_path = out_dir / "memray.bin"
    summary_path = out_dir / "memray.summary.txt"
    meta_path = out_dir / "memray.meta.json"
    flamegraph_path = out_dir / "memray.flamegraph.html"

    # memray run refuses to overwrite by default; clear prior artifacts.
    for p in (bin_path, summary_path, meta_path, flamegraph_path):
        if p.exists():
            p.unlink()

    # 1. Record the run.
    subprocess.check_call(
        [
            "memray",
            "run",
            "--aggregate",
            "--follow-fork",
            "--native",
            "-o",
            str(bin_path),
            "-m",
            "conda",
            *conda_args,
        ]
    )

    # 2. Top-20 allocators table. memray summary writes to stdout.
    with summary_path.open("w") as fh:
        subprocess.check_call(
            ["memray", "summary", "-r", "20", str(bin_path)],
            stdout=fh,
        )

    # 3. Top-line metadata (peak memory, total allocations, wall time).
    _dump_meta(bin_path, meta_path)

    # 4. HTML flamegraph.
    subprocess.check_call(
        [
            "memray",
            "flamegraph",
            "--force",
            "-o",
            str(flamegraph_path),
            str(bin_path),
        ]
    )

    print(f"wrote {bin_path}")
    print(f"wrote {summary_path}")
    print(f"wrote {meta_path}")
    print(f"wrote {flamegraph_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

