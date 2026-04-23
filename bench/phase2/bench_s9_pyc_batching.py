#!/usr/bin/env python3
"""S9 microbenchmark: pyc-compile subprocess fan-out (per-package vs batched).

Background (Phase-1 cProfile for W2):

    ncalls   tottime   cumtime  filename:lineno(function)
       186   0.015     9.474   subprocess.py:1178(communicate)  # pyc compile
       186             9.474   compile_multiple_pyc

One ``compile_multiple_pyc`` call per ``noarch: python`` package.
Each call spawns a fresh ``python -m compileall -q -l -i <file>``
subprocess. At 186 packages in W2, that's ~50 ms of per-subprocess
fixed cost (CPython startup + compileall import) per package, before
any .py file is touched.

S9 proposal: amortize the per-subprocess cost by batching multiple
packages into fewer subprocess invocations. ``compileall -j 0``
already parallelizes file compilation inside a subprocess, so one
call with N×K files should be significantly faster than N calls with
K files each.

This microbenchmark compares two strategies over the same P packages
× K files fixture:

  * ``per_package``  — P calls to ``compile_multiple_pyc``, one per
                        package (shipping behaviour)
  * ``batched``     — 1 call to ``compile_multiple_pyc`` with all
                        packages' files concatenated

Expected: ``batched`` is ~P-times faster for large P, bounded below
by the fixed ~150 ms of a single CPython subprocess + compileall
import + actual compile time.

``register_pyperf(runner, p)`` — both strategies at the same P.
``register_memray(p)``        — per_package invocation, for allocation.
"""
from __future__ import annotations

import os
import sys
import tempfile
from itertools import chain
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from fixtures import clear_pyc_cache, synthetic_py_packages

_FIXTURE_CACHE: dict = {}


def _setup(p: int, files_per_pkg: int = 10):
    key = (p, files_per_pkg)
    if key in _FIXTURE_CACHE:
        return _FIXTURE_CACHE[key]
    tmp_root = Path(
        os.environ.get("CONDA_BENCH_TMPDIR", tempfile.gettempdir())
    )
    tmp_root.mkdir(parents=True, exist_ok=True)
    packages, prefix = synthetic_py_packages(
        p, tmpdir=tmp_root, files_per_pkg=files_per_pkg,
    )
    _FIXTURE_CACHE[key] = (packages, prefix)
    return packages, prefix


def _bench_per_package(packages, prefix: str) -> None:
    """Current shipping pattern: one subprocess per package."""
    from conda.gateways.disk.create import compile_multiple_pyc

    clear_pyc_cache(packages)
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    for py_paths, pyc_paths in packages:
        compile_multiple_pyc(
            sys.executable,
            py_paths,
            pyc_paths,
            prefix,
            py_ver,
        )


def _bench_batched(packages, prefix: str) -> None:
    """Proposed: single subprocess for all packages' files."""
    from conda.gateways.disk.create import compile_multiple_pyc

    clear_pyc_cache(packages)
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    all_py = list(chain.from_iterable(py for py, _pyc in packages))
    all_pyc = list(chain.from_iterable(pyc for _py, pyc in packages))
    compile_multiple_pyc(
        sys.executable,
        all_py,
        all_pyc,
        prefix,
        py_ver,
    )


def register_memray(p: int) -> None:
    packages, prefix = _setup(p)
    _bench_per_package(packages, prefix)


def main() -> int:
    import pyperf

    def _forward_records(cmd, args):
        cmd.extend(("-N", str(args.count)))

    runner = pyperf.Runner(add_cmdline_args=_forward_records)
    runner.argparser.add_argument(
        "-N", "--count", type=int,
        default=int(os.environ.get("CONDA_BENCH_N", "30")),
        help="number of synthetic packages (default: 30)",
    )
    args = runner.parse_args()
    p = args.count

    runner.metadata["s9_p"] = str(p)
    packages, prefix = _setup(p)

    runner.bench_func(
        f"s9_pyc_per_package_p{p}",
        _bench_per_package,
        packages,
        prefix,
    )
    runner.bench_func(
        f"s9_pyc_batched_p{p}",
        _bench_batched,
        packages,
        prefix,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
