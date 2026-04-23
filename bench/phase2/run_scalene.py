#!/usr/bin/env python3
"""Run a Phase-2 suspect benchmark under Scalene.

Each ``bench_<id>.py`` in this directory exposes a ``register_memray(n)``
function that executes the suspect's hot path a representative number
of times. We reuse it as the Scalene target: same hot path, different
profiler.

Usage:
    python bench/phase2/run_scalene.py <suspect_id> -n <N>

Writes to ``data/phase2/<suspect_id>/``:
    scalene_n<N>.json       Scalene JSON profile

Scalene's line-level Python-vs-native split is particularly useful
for Phase-2 suspects where we suspect C-extension time is the hidden
cost — S11 (``conda_libmamba_solver.state.installed`` → libmambapy
boundary) and S2 (``MatchSpec.match`` in C). For S6 and S7 it's less
informative (those are dominated by syscalls/system time, already
attributed by cProfile).

See bench/run_scalene.py for the macOS conda-forge arm64e caveat.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATA = REPO_ROOT / "data" / "phase2"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("suspect_id")
    ap.add_argument("-n", "--records", type=int, default=5000)
    ns = ap.parse_args()

    out_dir = DATA / ns.suspect_id
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"scalene_n{ns.records}.json"
    if json_path.exists():
        json_path.unlink()

    here = Path(__file__).resolve().parent
    # Scalene refuses to emit data when the target runs for < 1 s. The
    # Phase-2 ``register_memray(n)`` entry points are tuned for memray's
    # lightweight overhead (typically 100 ms–1 s). For Scalene, we loop
    # the call enough times to cross the threshold by a comfortable
    # margin.
    driver_code = (
        "import sys\n"
        f"sys.path.insert(0, {str(here)!r})\n"
        f"from bench_{ns.suspect_id} import register_memray\n"
        "import time\n"
        "deadline = time.perf_counter() + 2.0\n"
        "iters = 0\n"
        f"while time.perf_counter() < deadline:\n"
        f"    register_memray({ns.records})\n"
        "    iters += 1\n"
        "print(f'scalene driver ran {iters} iterations')\n"
    )
    with tempfile.NamedTemporaryFile(
        "w", suffix=".py", delete=False, prefix="scalene_p2_",
    ) as fh:
        fh.write(driver_code)
        driver_path = fh.name

    try:
        cmd = [
            "scalene",
            "--cpu-only",
            "--profile-all",
            "--json",
            "--no-browser",
            "--outfile", str(json_path),
            driver_path,
        ]
        subprocess.check_call(cmd)
    finally:
        Path(driver_path).unlink(missing_ok=True)

    print(f"wrote {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
