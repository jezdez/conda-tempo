#!/usr/bin/env python3
"""S17 microbenchmark: libmamba index setup cost.

Background (Phase-4 stacked W2 cProfile, Linux ext4 container, 150-pkg
empty-prefix install):

    ncalls  tottime  cumtime  filename:lineno(function)
         1   4.054    4.054   index.py:696(_set_repo_priorities)
         1   4.053    4.053   index.py:586(_load_installed)

Both functions fire exactly once per ``conda install`` and together cost
~8 s of the 10.8 s W2 Linux wall time. On macOS W4 (cold pkgs dir, same
workload) they cost 1.78 s each = 3.56 s of 36.1 s. This is the single
biggest remaining cost on the post-solver warm-cache path that Track B
did not fix and is not a filesystem limit.

The hypothesis was that ``_set_repo_priorities`` (a thin Python loop
over ``self.repos`` that calls into libmambapy's
``set_repo_priority``) is mostly waiting on libmambapy's C++ work.
Similarly ``_load_installed`` wraps ``add_repo_from_packages``.

This benchmark:

1. Constructs a realistic ``LibMambaIndexHelper`` against the live
   conda-forge channel at the current platform's ``noarch`` subdir
   (one-time fixture, cached per worker).
2. Times ``_set_repo_priorities`` in isolation on the pre-built index
   to isolate the priority-assignment cost (expected: dominated by the
   C++ set_repo_priority calls).
3. Times ``_load_installed`` with synthetic ``PackageRecord`` lists of
   varied size (0, 150, 1 000, 5 000) to separate the Python
   ``_package_info_from_package_record`` per-record cost from the
   bulk ``add_repo_from_packages`` C++ cost.

Usage::

    python bench_s17_libmamba_index.py -N 150

    CONDA_BENCH_N=5000 python bench_s17_libmamba_index.py \\
        --output ../../data/phase2/s17/pyperf_n5000.json
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from fixtures import synthetic_prefix_records

_INDEX_CACHE: dict[tuple, object] = {}


def _build_index(n_installed: int = 0):
    """Build (or reuse within a worker) a ``LibMambaIndexHelper`` against
    the live conda-forge noarch channel. ``n_installed`` fake records
    are passed as ``installed_records`` to exercise ``_load_installed``.
    """
    key = ("cf_noarch", n_installed)
    if key in _INDEX_CACHE:
        return _INDEX_CACHE[key]

    # Import inside the function so pyperf's import overhead isn't
    # charged against sample timings.
    from conda.base.context import context  # noqa: F401
    from conda.models.channel import Channel
    from conda_libmamba_solver.index import LibMambaIndexHelper

    channels = [Channel("conda-forge")]
    subdirs = ("noarch",)
    installed = synthetic_prefix_records(n_installed) if n_installed else ()

    helper = LibMambaIndexHelper(
        channels=channels,
        subdirs=subdirs,
        installed_records=installed,
    )
    _INDEX_CACHE[key] = helper
    return helper


def _bench_set_repo_priorities(helper) -> None:
    helper._set_repo_priorities()


def _bench_load_installed(args) -> None:
    helper, records = args
    # Not quite hermetic: _load_installed mutates helper.db by adding
    # a new repo. We accept this because each sample adds one more
    # "installed" repo and libmambapy tolerates many. The cost we're
    # measuring is add_repo_from_packages, which is what matters.
    helper._load_installed(records)


def register_memray(n: int) -> None:
    helper = _build_index(n_installed=n)
    for _ in range(10):
        _bench_set_repo_priorities(helper)


def main() -> int:
    import pyperf

    def _forward_records(cmd, args):
        cmd.extend(("-N", str(args.records)))

    runner = pyperf.Runner(add_cmdline_args=_forward_records)
    runner.argparser.add_argument(
        "-N",
        "--records",
        type=int,
        default=int(os.environ.get("CONDA_BENCH_N", "150")),
        help="synthetic installed-record count (default: 150, or $CONDA_BENCH_N)",
    )
    args = runner.parse_args()
    n = args.records

    runner.metadata["s17_n_installed"] = str(n)

    # Bench A: _set_repo_priorities on an index with n_installed=0 so
    # self.repos contains only the conda-forge channel's subdirs. This
    # isolates the priority-assignment cost independent of installed
    # set size.
    helper = _build_index(n_installed=0)
    runner.bench_func(
        f"s17_set_repo_priorities_n_repos{len(helper.repos)}",
        _bench_set_repo_priorities,
        helper,
    )

    # Bench B: _load_installed with n fresh records. Each sample adds
    # one more "installed" repo so the measurement includes whatever
    # cumulative-state cost libmambapy adds, but the per-call dominant
    # cost is add_repo_from_packages(n records).
    records = synthetic_prefix_records(n) if n else ()
    runner.bench_func(
        f"s17_load_installed_n{n}",
        _bench_load_installed,
        (helper, records),
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
