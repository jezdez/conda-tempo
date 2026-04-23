#!/usr/bin/env python3
"""S11 microbenchmark: ``conda_libmamba_solver.state.SolverInputState.installed``.

Background (Phase-1 cProfile for W3):

    ncalls  tottime  cumtime  filename:lineno(function)
     10032   3.471   41.761   state.py:220(installed)
     10963  20.023   38.830   {built-in method builtins.sorted}
  50170781   8.128   18.855   <frozen _collections_abc>:897(__iter__)

The property does, per access::

    return MappingProxyType(dict(sorted(self.prefix_data._prefix_records.items())))

which rebuilds + sorts the full installed-records dict from scratch every
time. ``_specs_to_request_jobs_add`` hits this property many times per
spec (see solver.py:442, 452, 458).

This microbenchmark isolates the per-access cost at parameterized N.

Expected Phase-2 confirmation: per-access cost scales as O(N log N) with
a large constant from tuple-key comparison, so overall solver behavior
is O(M × N log N) where M is the number of ``in_state.installed``
accesses.

Usage as a pyperf script (directly)::

    CONDA_BENCH_N=5000 python bench_s11_libmamba_installed.py \\
        --output ../../data/phase2/s11/pyperf_n5000.json

    # pyperf compare_to baseline.json candidate.json
    # pyperf stats pyperf_n5000.json

Usage under ``run_memray.py``::

    python run_memray.py s11_libmamba_installed -n 5000

The file exposes the private helpers ``_solver_state(n)`` and
``_bench_installed(sis)`` so ``run_memray.py`` and ad-hoc scripts can
reuse the fixture.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Make ``fixtures`` importable both when run as a file (python
# bench_s11_libmamba_installed.py) and when imported as a module by
# run_memray.py.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from fixtures import synthetic_prefix

_STATE_CACHE: dict = {}
_PREFIX_CACHE: dict = {}


def _solver_state(n: int):
    """Build (or reuse within a worker) a ``SolverInputState`` over ``n`` records."""
    if n in _STATE_CACHE:
        return _STATE_CACHE[n]

    tmp_root = Path(os.environ.get("CONDA_BENCH_TMPDIR", tempfile.gettempdir()))
    tmp_root.mkdir(parents=True, exist_ok=True)
    prefix = synthetic_prefix(n, tmpdir=tmp_root)
    _PREFIX_CACHE[n] = prefix

    # Import inside the function so pyperf's import cost isn't charged
    # to sample timings.
    from conda_libmamba_solver.state import SolverInputState

    sis = SolverInputState(str(prefix))
    _STATE_CACHE[n] = sis
    return sis


def _bench_installed(sis) -> None:
    """The unit of work: a single ``.installed`` property access."""
    _ = sis.installed


def register_memray(n: int) -> None:
    """Run 100 accesses under the active memray tracer.

    Invoked by ``run_memray.py``. 100 is large enough to see allocator
    behavior, small enough to keep the aggregated .bin reasonable at any N.
    """
    sis = _solver_state(n)
    for _ in range(100):
        _bench_installed(sis)


def main() -> int:
    import pyperf

    # pyperf spawns fresh worker subprocesses for each sample. Custom args
    # added to runner.argparser are parsed in the master but not
    # automatically re-injected into the worker command line — we need an
    # explicit ``add_cmdline_args`` callback for that.
    def _forward_records(cmd, args):
        cmd.extend(("-N", str(args.records)))

    runner = pyperf.Runner(add_cmdline_args=_forward_records)
    runner.argparser.add_argument(
        "-N", "--records", type=int,
        default=int(os.environ.get("CONDA_BENCH_N", "5000")),
        help="synthetic prefix size (default: 5000, or $CONDA_BENCH_N)",
    )
    args = runner.parse_args()
    n = args.records

    runner.metadata["s11_n"] = str(n)
    sis = _solver_state(n)
    runner.bench_func(
        f"s11_installed_access_n{n}",
        _bench_installed,
        sis,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
