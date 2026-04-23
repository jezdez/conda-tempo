#!/usr/bin/env python3
"""Sweep a Phase 2 suspect benchmark over a list of prefix sizes.

Each ``bench_<id>.py`` in this directory is a self-contained pyperf
script (reads ``CONDA_BENCH_N`` from the environment, writes to
``--output``). This orchestrator just invokes the script once per N
value and places the JSON in ``data/phase2/<id>/pyperf_n<N>.json``.

Usage::

    python bench/phase2/run_pyperf.py <suspect_id> [--sizes 1000 5000 10000] \\
        [--mode fast|full]

``--mode fast`` passes ``--fast`` to pyperf (fewer samples, ~5 s per N,
good for smoke tests). ``--mode full`` is pyperf defaults (~20+ runs
with full calibration, multi-minute per N, for committed data).

The script exits non-zero if any N fails; earlier N values are kept.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent.parent
DATA = REPO_ROOT / "data" / "phase2"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("suspect_id", help="e.g. 's11_libmamba_installed'")
    ap.add_argument(
        "--sizes", nargs="+", type=int, default=[1000, 5000, 10000],
        help="prefix sizes (default: 1000 5000 10000)",
    )
    ap.add_argument(
        "--mode", choices=["fast", "full"], default="full",
        help="pyperf sample budget (default: full)",
    )
    ns = ap.parse_args()

    script = HERE / f"bench_{ns.suspect_id}.py"
    if not script.is_file():
        sys.exit(f"error: {script} not found")

    out_dir = DATA / ns.suspect_id
    out_dir.mkdir(parents=True, exist_ok=True)

    failed = []
    for n in ns.sizes:
        out_path = out_dir / f"pyperf_n{n}.json"
        if out_path.exists():
            out_path.unlink()

        cmd = [
            sys.executable,
            str(script),
            "--output", str(out_path),
            "-N", str(n),
        ]
        if ns.mode == "fast":
            cmd.append("--fast")

        print(f"=== pyperf: {ns.suspect_id} N={n} ({ns.mode}) ===")
        print(f"  {' '.join(cmd)}")
        rc = subprocess.call(cmd)
        if rc != 0:
            print(f"  !! N={n} exited {rc}")
            failed.append(n)
        elif out_path.exists():
            print(f"  -> {out_path}")

    if failed:
        print(f"\nFailed sizes: {failed}", file=sys.stderr)
        return 1

    print(f"\nAll sizes complete. Data in {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
