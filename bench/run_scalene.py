#!/usr/bin/env python3
"""Scalene wrapper for a single conda invocation.

Usage:
    python bench/run_scalene.py <workload> -- <conda-args...>

Example:
    python bench/run_scalene.py w2 -- create -n prof_tmp -y python=3.13 pandas

Writes to data/phase1/<workload>/:
    scalene.json            profile in Scalene's own JSON schema (keep for
                            later re-rendering or cross-commit diffs)

Scalene's unique value vs. cProfile + memray:

  * cProfile: function-level CPU time, no distinction between Python
    and native code.
  * memray: allocations, but does not correlate with CPU time.
  * Scalene: **per-line** CPU time split into ``python_percent``,
    ``native_percent``, ``system_percent``. Answers "how much of the
    cost is in the C extension vs. Python-level orchestration" — which
    no other tool in this harness does.

Known limitation: scalene's conda-forge build for Python 3.13 on
macOS 26 fails to load due to an ``arm64e.old`` ABI mismatch. This
harness is intended to run inside the Linux Docker container at
``docker/Dockerfile``, where the conda-forge Linux build works
correctly. The bench/README section "Scalene on macOS" documents the
macOS limitation.
"""
from __future__ import annotations

import argparse
import subprocess
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
    json_path = out_dir / "scalene.json"
    if json_path.exists():
        json_path.unlink()

    # Scalene's ``run`` subcommand profiles one program invocation and
    # writes the JSON to --outfile. We want to profile ``python -m conda
    # <args>``, so we launch scalene on a tiny ``driver.py`` that execs
    # conda.cli.main via runpy — same pattern as run_cprofile.py.
    #
    # Scalene flags chosen:
    #   --cpu-only   : skip memory profiling (memray already covers it;
    #                  Scalene's memory mode adds noticeable overhead)
    #   --profile-all: also profile conda's dependencies (libmambapy
    #                  bindings especially) — otherwise Scalene skips
    #                  everything outside the target file.
    #
    # We create the driver inline rather than shipping a file, so the
    # harness is self-contained.
    driver_code = (
        "import runpy, sys\n"
        f"sys.argv = ['conda'] + {conda_args!r}\n"
        "runpy.run_module('conda', run_name='__main__', alter_sys=True)\n"
    )
    import tempfile
    with tempfile.NamedTemporaryFile(
        "w", suffix=".py", delete=False, prefix="scalene_driver_",
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
    print(f"view:  scalene view --cli {json_path}")
    print(f"       scalene view      {json_path}   # opens browser")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
